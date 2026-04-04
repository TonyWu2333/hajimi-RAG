#!/usr/bin/env python3
"""
txt_to_chroma.py  —  增量版

【增量更新】启动时对比文件 MD5，只处理新增/变更的文件，跳过未变化的文件。

依赖安装：
    pip install chromadb watchdog dashscope

用法：
    python txt_to_chroma.py --source ./docs --db ./chroma_db
"""

import argparse
import hashlib
import json
import sys
import time
import logging
import os
import yaml
from http import HTTPStatus
from pathlib import Path

from extractText.extract import extract as extract_text

try:
    import chromadb
    from chromadb import EmbeddingFunction, Embeddings
except ImportError:
    sys.exit("缺少依赖，请先执行：pip install chromadb")

try:
    import dashscope
except ImportError:
    sys.exit("缺少依赖，请先执行：pip install dashscope")

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
except ImportError:
    sys.exit("缺少依赖，请先执行：pip install watchdog")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# 从环境变量获取API key
DASHSCOPE_API_KEY          = os.environ.get("DASHSCOPE_API_KEY")
if not DASHSCOPE_API_KEY:
    raise ValueError("环境变量 DASHSCOPE_API_KEY 未设置")
DASHSCOPE_EMBED_MODEL      = "text-embedding-v4"
DASHSCOPE_EMBED_DIMENSIONS = 1024
DASHSCOPE_BATCH_SIZE       = 10

COLLECTION_NAME = "txt_chunks"
CHUNK_SEPARATORS = ["\n\n", "\n", "。", "！", "？", " ", ""]
CHUNK_MAX_SIZE   = 512   # 每个 chunk 的最大字符数
CHUNK_OVERLAP    = 50    # 相邻 chunk 重叠字符数
HASH_FILE_NAME  = ".file_hashes.json"

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".xlsm", ".xls", ".ods", ".pptx", ".epub",
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log",
    ".json", ".jsonl", ".xml", ".html", ".htm", ".yaml", ".yml",
}

# ── 文件指纹管理 ──────────────────────────────────────────────────────────────

class HashStore:
    """持久化保存 {abs_path: md5}，用于增量判断。"""

    def __init__(self, db_path: str) -> None:
        self._path  = Path(db_path) / HASH_FILE_NAME
        self._store: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._store, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _md5(file_path: str) -> str:
        h = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def is_changed(self, abs_path: str) -> bool:
        try:
            current = self._md5(abs_path)
        except OSError:
            return False
        return self._store.get(abs_path) != current

    def update(self, abs_path: str) -> None:
        try:
            self._store[abs_path] = self._md5(abs_path)
        except OSError:
            pass
        self._save()

    def remove(self, abs_path: str) -> None:
        self._store.pop(abs_path, None)
        self._save()


# ── DashScope Embedder ────────────────────────────────────────────────────────

class DashScopeEmbedder(EmbeddingFunction):
    def __init__(self) -> None:
        dashscope.api_key = DASHSCOPE_API_KEY

    def __call__(self, input: list[str]) -> Embeddings:
        all_embeddings: Embeddings = []
        for start in range(0, len(input), DASHSCOPE_BATCH_SIZE):
            batch = input[start : start + DASHSCOPE_BATCH_SIZE]
            resp = dashscope.TextEmbedding.call(
                model=DASHSCOPE_EMBED_MODEL,
                input=batch,
                dimensions=DASHSCOPE_EMBED_DIMENSIONS,
            )
            if resp.status_code != HTTPStatus.OK:
                raise RuntimeError(f"Embedding 调用失败: {resp.status_code} {resp.message}")
            sorted_emb = sorted(resp.output["embeddings"], key=lambda x: x["text_index"])
            all_embeddings.extend([e["embedding"] for e in sorted_emb])
        return all_embeddings


# ── ChromaDB ──────────────────────────────────────────────────────────────────

def get_collection(db_path: str) -> chromadb.Collection:
    client = chromadb.PersistentClient(path=db_path)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=DashScopeEmbedder(),
        metadata={"hnsw:space": "cosine"},
    )


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def make_chunk_id(file_path: str, chunk_index: int) -> str:
    path_hash = hashlib.sha1(file_path.encode()).hexdigest()[:16]
    return f"{path_hash}_{chunk_index}"


def _recursive_split(text: str, separators: list[str], max_size: int) -> list[str]:
    """递归地用优先级从高到低的分隔符切分，直到每块不超过 max_size。"""
    if len(text) <= max_size or not separators:
        return [text] if text.strip() else []

    sep = separators[0]
    parts = text.split(sep) if sep else list(text)

    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= max_size:
            result.append(part)
        else:
            # 当前分隔符切出来还是太大，用下一级继续递归
            result.extend(_recursive_split(part, separators[1:], max_size))
    return result


def _merge_with_overlap(chunks: list[str], max_size: int, overlap: int) -> list[str]:
    """将过短的相邻 chunk 合并，并在边界处添加重叠。"""
    if not chunks:
        return []

    merged = []
    current = chunks[0]

    for next_chunk in chunks[1:]:
        # 能合并就合并
        if len(current) + len(next_chunk) + 1 <= max_size:
            current = current + " " + next_chunk
        else:
            merged.append(current)
            # 取上一个 chunk 末尾作为重叠前缀
            prefix = current[-overlap:] if overlap and len(current) > overlap else ""
            current = (prefix + " " + next_chunk).strip() if prefix else next_chunk

    merged.append(current)
    return merged


def split_text(text: str) -> list[str]:
    raw    = _recursive_split(text, CHUNK_SEPARATORS, CHUNK_MAX_SIZE)
    merged = _merge_with_overlap(raw, CHUNK_MAX_SIZE, CHUNK_OVERLAP)
    return [c for c in merged if c.strip()]


def _delete_chunks(collection: chromadb.Collection, abs_path: str) -> None:
    try:
        results = collection.get(where={"file_path": abs_path})
        if results and results["ids"]:
            collection.delete(ids=results["ids"])
    except Exception as e:
        log.warning("删除切片出错 %s: %s", abs_path, e)


# ── 核心操作 ──────────────────────────────────────────────────────────────────

def index_file(
    collection: chromadb.Collection,
    hash_store: HashStore,
    file_path: str,
    force: bool = False,
) -> None:
    abs_path = str(Path(file_path).resolve())

    # 增量核心：MD5 未变则跳过
    if not force and not hash_store.is_changed(abs_path):
        log.debug("未变化，跳过：%s", abs_path)
        return

    try:
        text = extract_text(abs_path)
    except FileNotFoundError as e:
        log.warning("文件不存在 %s: %s", abs_path, e)
        return
    except ValueError as e:
        log.warning("不支持的格式 %s: %s", abs_path, e)
        return
    except Exception as e:
        log.warning("提取文本失败 %s: %s", abs_path, e)
        return

    chunks = split_text(text)
    if not chunks:
        log.info("文件为空，跳过：%s", abs_path)
        return

    _delete_chunks(collection, abs_path)

    ids       = [make_chunk_id(abs_path, i) for i in range(len(chunks))]
    metadatas = [
        {"file_path": abs_path, "chunk_index": i, "total_chunks": len(chunks)}
        for i in range(len(chunks))
    ]

    BATCH = 512
    for start in range(0, len(ids), BATCH):
        collection.upsert(
            ids=ids[start : start + BATCH],
            documents=chunks[start : start + BATCH],
            metadatas=metadatas[start : start + BATCH],
        )

    hash_store.update(abs_path)
    log.info("已索引 %s  →  %d 个切片", abs_path, len(chunks))


def remove_file(
    collection: chromadb.Collection,
    hash_store: HashStore,
    file_path: str,
) -> None:
    abs_path = str(Path(file_path).resolve())
    try:
        results = collection.get(where={"file_path": abs_path})
        if results and results["ids"]:
            collection.delete(ids=results["ids"])
            log.info("已删除 %s 的 %d 个切片", abs_path, len(results["ids"]))
    except Exception as e:
        log.warning("删除 %s 出错: %s", abs_path, e)
    hash_store.remove(abs_path)


# ── 增量初始扫描 ──────────────────────────────────────────────────────────────

def initial_index(
    collection: chromadb.Collection,
    hash_store: HashStore,
    source_dir: str,
) -> None:
    source      = Path(source_dir).resolve()

    txt_files = {
        str(f.resolve())
        for f in source.rglob("*")
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    }
    known_files = set(hash_store._store.keys())

    # 清理已从磁盘消失的文件
    for gone in known_files - txt_files:
        log.info("[已消失] 清理：%s", gone)
        remove_file(collection, hash_store, gone)

    to_index = [p for p in txt_files if hash_store.is_changed(p)]
    skipped  = len(txt_files) - len(to_index)

    log.info(
        "扫描：共 %d 个文件 | 需索引 %d 个 | 跳过 %d 个（无变化）",
        len(txt_files), len(to_index), skipped,
    )

    for path in to_index:
        index_file(collection, hash_store, path, force=True)

    log.info("初始索引完成。")


# ── Watchdog ──────────────────────────────────────────────────────────────────

class TxtChangeHandler(FileSystemEventHandler):

    def __init__(self, collection: chromadb.Collection, hash_store: HashStore) -> None:
        super().__init__()
        self.collection = collection
        self.hash_store = hash_store

    @staticmethod
    def _is_txt(path: str) -> bool:
        return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_txt(event.src_path):
            log.info("[新增] %s", event.src_path)
            time.sleep(0.3)
            index_file(self.collection, self.hash_store, event.src_path, force=True)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_txt(event.src_path):
            log.info("[修改] %s", event.src_path)
            time.sleep(0.3)
            index_file(self.collection, self.hash_store, event.src_path, force=True)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_txt(event.src_path):
            log.info("[删除] %s", event.src_path)
            remove_file(self.collection, self.hash_store, event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._is_txt(event.src_path):
            log.info("[移动-旧] %s", event.src_path)
            remove_file(self.collection, self.hash_store, event.src_path)
        if self._is_txt(event.dest_path):
            log.info("[移动-新] %s", event.dest_path)
            time.sleep(0.3)
            index_file(self.collection, self.hash_store, event.dest_path, force=True)


# ── 主函数 ────────────────────────────────────────────────────────────────────

def main(source_dir: str, db_path: str) -> None:
    source = Path(source_dir).resolve()
    if not source.is_dir():
        sys.exit(f"源目录不存在：{source}")

    db_abs = str(Path(db_path).resolve())
    Path(db_abs).mkdir(parents=True, exist_ok=True)

    log.info("源目录：%s", source)
    log.info("数据库：%s", db_abs)

    collection = get_collection(db_abs)
    hash_store = HashStore(db_abs)

    initial_index(collection, hash_store, str(source))

    handler  = TxtChangeHandler(collection, hash_store)
    observer = Observer()
    observer.schedule(handler, str(source), recursive=True)
    observer.start()
    log.info("开始监控目录变化，按 Ctrl+C 退出……")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("收到退出信号，正在停止……")
        observer.stop()

    observer.join()
    log.info("已退出。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="将 txt 文件切分并存入 ChromaDB，实时监控（增量更新）。"
    )
    parser.add_argument(
        "--source",
        default=r"./workspace",
        help="源目录路径（默认：./workspace）",
    )
    parser.add_argument(
        "--db",
        default="./chroma_db",
        help="ChromaDB 存储目录（默认：./chroma_db）",
    )
    args = parser.parse_args()
    main(args.source, args.db)