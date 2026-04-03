"""
extract_plaintext.py
读取纯文本文件（.txt / .md / .csv / .log / .json / .xml 等）并返回字符串。

无第三方依赖，仅使用标准库。

用法:
    from extract_plaintext import extract_plaintext
    text = extract_plaintext("path/to/file.txt")
"""

from pathlib import Path

# 公认的纯文本扩展名（不在列表中的文件也会尝试读取，仅给出警告）
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv",
    ".log", ".json", ".jsonl", ".xml", ".html", ".htm",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h",
    ".go", ".rs", ".rb", ".sh", ".bat", ".sql",
}

# 常见文本编码，按优先级尝试
_ENCODINGS = ["utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"]


def extract_plaintext(
    file_path: str,
    encoding: str | None = None,
    errors: str = "replace",
) -> str:
    """
    读取纯文本文件并返回其内容。

    自动尝试多种编码（utf-8 → gbk → latin-1），也可手动指定。

    Args:
        file_path: 文件路径。
        encoding:  强制使用的编码（None 表示自动检测）。
        errors:    编码错误处理策略，传给 open()，默认 "replace"。

    Returns:
        文件的完整文本内容。

    Raises:
        FileNotFoundError: 文件不存在。
        IsADirectoryError: 路径是目录而非文件。
        UnicodeDecodeError: 所有编码均无法解码（仅当 errors="strict" 时）。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    if path.is_dir():
        raise IsADirectoryError(f"路径是目录，不是文件: {file_path}")

    if path.suffix.lower() not in _TEXT_EXTENSIONS:
        import warnings
        warnings.warn(
            f"扩展名 '{path.suffix}' 不在已知纯文本列表中，仍尝试读取。",
            UserWarning,
            stacklevel=2,
        )

    if encoding:
        with path.open(encoding=encoding, errors=errors) as f:
            return f.read()

    # 自动尝试多种编码
    last_exc: Exception | None = None
    for enc in _ENCODINGS:
        try:
            with path.open(encoding=enc, errors="strict") as f:
                return f.read()
        except (UnicodeDecodeError, LookupError) as e:
            last_exc = e
            continue

    # 最后兜底：latin-1 + replace，不会抛出解码错误
    with path.open(encoding="latin-1", errors="replace") as f:
        return f.read()


if __name__ == "__main__":
    import sys
    if len(sys.argv) not in (2, 3):
        print("用法: python extract_plaintext.py <file> [encoding]")
        sys.exit(1)
    enc = sys.argv[2] if len(sys.argv) == 3 else None
    print(extract_plaintext(sys.argv[1], encoding=enc))