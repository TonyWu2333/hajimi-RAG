# bm25_search.py
"""
BM25 搜索引擎 - 在指定文件夹中搜索相关文档

用法:
    from bm25_search import search
    results = search("你的问题", folder="./workspace")
    
或命令行:
    python bm25_search.py "你的问题" --folder ./workspace --top 5
"""

import os
import sys
import argparse
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import json

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    print("请先安装 rank-bm25: pip install rank-bm25")
    sys.exit(1)

try:
    import jieba
except ImportError:
    print("请先安装 jieba: pip install jieba")
    sys.exit(1)

# 导入文本提取函数
from extractText.extract import extract


class BM25Searcher:
    """BM25 搜索引擎"""
    
    def __init__(self, folder_path: str = "./workspace", supported_extensions: Optional[List[str]] = None):
        """
        初始化搜索引擎
        
        Args:
            folder_path: 要搜索的文件夹路径，默认为 ./workspace
            supported_extensions: 支持的文件扩展名列表，默认使用 extract 模块支持的所有格式
        """
        self.folder_path = Path(folder_path)
        if not self.folder_path.exists():
            raise FileNotFoundError(f"文件夹不存在: {folder_path}")
        
        # 支持的文件格式（与 extract 模块保持一致）
        self.supported_extensions = supported_extensions or [
            '.pdf', '.docx', '.doc', '.xlsx', '.xlsm', '.xls', '.ods',
            '.pptx', '.ppt', '.epub', '.txt', '.md', '.csv', '.log',
            '.json', '.xml', '.html', '.htm', '.yaml', '.yml'
        ]
        
        self.documents: List[Dict] = []  # 存储文档信息: {path, text, tokens}
        self.bm25: Optional[BM25Okapi] = None
        self.corpus_tokens: List[List[str]] = []
        
    def collect_files(self) -> List[Path]:
        """收集文件夹中所有支持格式的文件"""
        files = []
        for ext in self.supported_extensions:
            # 递归搜索所有子文件夹
            files.extend(self.folder_path.rglob(f"*{ext}"))
        return sorted(files)
    
    def tokenize(self, text: str) -> List[str]:
        """
        中文分词 + 英文小写化
        
        Args:
            text: 原始文本
            
        Returns:
            分词后的 token 列表
        """
        if not text:
            return []
        
        # 使用 jieba 进行中文分词
        words = jieba.lcut(text)
        
        # 过滤掉空白字符和过短的词（可选）
        tokens = [word.strip().lower() for word in words if word.strip()]
        
        # 简单的英文分词（保留 jieba 已处理的英文词）
        # jieba 会自动处理英文，这里不需要额外处理
        
        return tokens
    
    def load_documents(self, max_file_size_mb: int = 50) -> int:
        """
        加载并处理文件夹中的所有文档
        
        Args:
            max_file_size_mb: 最大文件大小（MB），超过此大小的文件将被跳过
            
        Returns:
            成功加载的文档数量
        """
        files = self.collect_files()
        print(f"找到 {len(files)} 个文件")
        
        loaded_count = 0
        skipped_count = 0
        
        for file_path in files:
            # 检查文件大小
            file_size_mb = file_path.stat().st_size / (1024 * 1024)
            if file_size_mb > max_file_size_mb:
                print(f"跳过（文件过大 {file_size_mb:.1f}MB）: {file_path}")
                skipped_count += 1
                continue
            
            try:
                # 提取文本内容
                print(f"正在加载: {file_path.name} ({file_size_mb:.1f}MB)")
                text = extract(str(file_path))
                
                if text and len(text.strip()) > 0:
                    # 分词
                    tokens = self.tokenize(text)
                    
                    if tokens:
                        self.documents.append({
                            'path': str(file_path),
                            'name': file_path.name,
                            'size_mb': file_size_mb,
                            'text_length': len(text),
                            'token_count': len(tokens)
                        })
                        self.corpus_tokens.append(tokens)
                        loaded_count += 1
                    else:
                        print(f"  警告: 分词后为空 - {file_path.name}")
                else:
                    print(f"  警告: 文本内容为空 - {file_path.name}")
                    
            except Exception as e:
                print(f"  错误: 无法处理 {file_path.name}: {e}")
                skipped_count += 1
        
        print(f"\n加载完成: {loaded_count} 个文档, 跳过 {skipped_count} 个")
        
        if loaded_count > 0:
            # 构建 BM25 索引
            self.bm25 = BM25Okapi(self.corpus_tokens)
            print(f"BM25 索引构建完成")
        else:
            print("警告: 没有成功加载任何文档")
            
        return loaded_count
    
    def search(self, query: str, top_k: int = 5) -> List[Tuple[Dict, float]]:
        """
        搜索与查询最相关的文档
        
        Args:
            query: 查询字符串
            top_k: 返回的结果数量
            
        Returns:
            [(文档信息字典, BM25分数), ...] 按分数降序排列
        """
        if not self.bm25 or not self.documents:
            print("请先调用 load_documents() 加载文档")
            return []
        
        # 对查询进行分词
        query_tokens = self.tokenize(query)
        
        if not query_tokens:
            print("查询为空或分词后无有效内容")
            return []
        
        # 计算 BM25 分数
        scores = self.bm25.get_scores(query_tokens)
        
        # 获取 top_k 个结果
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        
        results = []
        for idx in top_indices:
            if scores[idx] > 0:
                results.append((self.documents[idx], scores[idx]))
        
        return results


def search(query: str, folder: str = "./workspace", top_k: int = 5, max_file_size_mb: int = 50) -> List[Tuple[str, float]]:
    """
    便捷搜索函数
    
    Args:
        query: 搜索问题
        folder: 要搜索的文件夹路径，默认为 ./workspace
        top_k: 返回的结果数量
        max_file_size_mb: 最大文件大小（MB）
        
    Returns:
        [(文件名, BM25分数), ...] 按分数降序排列
        
    Example:
        results = search("人工智能的发展趋势", folder="./workspace", top_k=5)
        for filename, score in results:
            print(f"{filename}: {score:.4f}")
    """
    searcher = BM25Searcher(folder)
    searcher.load_documents(max_file_size_mb)
    results = searcher.search(query, top_k)
    return [(r[0]['name'], r[1]) for r in results]


def print_results(results: List[Tuple[Dict, float]], query: str):
    """格式化打印搜索结果"""
    print("\n" + "="*80)
    print(f"搜索问题: {query}")
    print("="*80)
    
    if not results:
        print("未找到相关文档")
        return
    
    print(f"\n找到 {len(results)} 个相关文档:\n")
    
    for i, (doc, score) in enumerate(results, 1):
        print(f"{i}. {doc['name']}")
        print(f"   路径: {doc['path']}")
        print(f"   分数: {score:.4f}")
        print(f"   大小: {doc['size_mb']:.2f} MB")
        print(f"   文本长度: {doc['text_length']:,} 字符")
        print(f"   词数: {doc['token_count']:,}")
        print()


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description='BM25 文档搜索引擎')
    parser.add_argument('query', type=str, help='搜索问题')
    parser.add_argument('--folder', '-f', type=str, default='./workspace', help='要搜索的文件夹路径 (默认: ./workspace)')
    parser.add_argument('--top', '-k', type=int, default=5, help='返回结果数量 (默认: 5)')
    parser.add_argument('--max-size', '-s', type=int, default=50, help='最大文件大小 MB (默认: 50)')
    parser.add_argument('--debug', action='store_true', help='显示详细调试信息')
    
    args = parser.parse_args()
    
    try:
        # 创建搜索引擎实例
        searcher = BM25Searcher(args.folder)
        
        # 加载文档
        doc_count = searcher.load_documents(args.max_size)
        
        if doc_count == 0:
            print("没有找到可搜索的文档")
            return
        
        # 执行搜索
        results = searcher.search(args.query, args.top)
        
        # 打印结果
        print_results(results, args.query)
        
        # 调试信息
        if args.debug and results:
            print("\n调试信息:")
            for doc, score in results:
                print(f"\n{doc['name']}:")
                print(f"  原始文本片段（前200字符）:")
                # 需要重新读取文件来显示片段
                try:
                    text = extract(doc['path'])
                    preview = text[:200].replace('\n', ' ')
                    print(f"  {preview}...")
                except:
                    pass
                    
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()