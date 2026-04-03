"""
extract.py
统一文本提取入口——根据文件扩展名自动分派到对应模块。

支持格式:
    PDF       .pdf
    Word      .docx  .doc
    Excel     .xlsx  .xlsm  .xls  .ods
    PPT       .pptx  .ppt
    EPUB      .epub
    纯文本    .txt .md .csv .log .json .xml 等

依赖安装（一次性）:
    pip install pypdf python-docx python-pptx openpyxl xlrd odfpy pandas \
                ebooklib beautifulsoup4

用法:
    from extract import extract
    text = extract("report.pdf")
    text = extract("slides.pptx")
    text = extract("data.xlsx")
"""

from pathlib import Path

# 扩展名 → 模块内函数映射（延迟导入，避免缺包时整体崩溃）
_DISPATCH: dict[str, tuple[str, str]] = {
    # ext: (module_file, function_name)
    ".pdf":  ("extract_pdf",       "extract_pdf"),
    ".docx": ("extract_word",      "extract_word"),
    ".xlsx": ("extract_excel",     "extract_excel"),
    ".xlsm": ("extract_excel",     "extract_excel"),
    ".xls":  ("extract_excel",     "extract_excel"),
    ".ods":  ("extract_excel",     "extract_excel"),
    ".pptx": ("extract_ppt",       "extract_ppt"),
    ".epub": ("extract_epub",      "extract_epub"),
    # ".ppt":  ("extract_ppt",       "extract_ppt"),
    # ".doc":  ("extract_word",      "extract_word"),
}

_PLAINTEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv",
    ".log", ".json", ".jsonl", ".xml", ".html", ".htm",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h",
    ".go", ".rs", ".rb", ".sh", ".bat", ".sql",
}


def extract(file_path: str, **kwargs) -> str:
    """
    自动识别文件格式并提取文本。

    Args:
        file_path: 任意受支持格式的文件路径。
        **kwargs:  传递给底层提取函数的额外参数
                   （如 extract_plaintext 的 encoding）。

    Returns:
        提取的纯文本字符串。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 不支持的文件格式。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    suffix = path.suffix.lower()

    if suffix in _DISPATCH:
        module_name, func_name = _DISPATCH[suffix]
        import importlib
        mod = importlib.import_module(f'.{module_name}', package='extractText')
        func = getattr(mod, func_name)
        return func(file_path, **kwargs)

    if suffix in _PLAINTEXT_EXTENSIONS or not suffix:
        from extractText.extract_plaintext import extract_plaintext
        return extract_plaintext(file_path, **kwargs)

    raise ValueError(
        f"不支持的文件格式: '{suffix}'。\n"
        f"支持: PDF / Word / Excel / PPT / EPUB / 纯文本"
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python extract.py <file>")
        sys.exit(1)
    result = extract(sys.argv[1])
    print(result)