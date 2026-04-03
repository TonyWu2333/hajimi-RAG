"""
extract_epub.py
提取 EPUB 电子书中的纯文本。

依赖（二选一，优先 ebooklib）:
    pip install ebooklib beautifulsoup4
    或者系统安装 pandoc（apt install pandoc）

用法:
    from extract_epub import extract_epub
    text = extract_epub("path/to/file.epub")
"""

from pathlib import Path


def _extract_via_ebooklib(path: Path) -> str:
    """使用 ebooklib + BeautifulSoup 提取 EPUB 文本。"""
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup

    book = epub.read_epub(str(path))
    parts = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        text = soup.get_text(separator="\n")
        # 去除连续空行
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if lines:
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _extract_via_pandoc(path: Path) -> str:
    """使用 pandoc 提取 EPUB 文本（fallback）。"""
    import subprocess
    result = subprocess.run(
        ["pandoc", str(path), "-t", "plain", "--wrap=none"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pandoc 转换失败: {result.stderr.strip()}")
    return result.stdout


def extract_epub(file_path: str) -> str:
    """
    提取 EPUB 文件的文本内容。

    优先使用 ebooklib（纯 Python），若未安装则退回到 pandoc。

    Args:
        file_path: EPUB 文件路径。

    Returns:
        提取的纯文本字符串。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 不是 EPUB 文件。
        RuntimeError: 所有提取方式均失败。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    if path.suffix.lower() != ".epub":
        raise ValueError(f"不是 EPUB 文件: {file_path}")

    # 优先 ebooklib
    try:
        return _extract_via_ebooklib(path)
    except ImportError:
        pass  # 未安装，尝试 pandoc

    # 回退到 pandoc
    try:
        return _extract_via_pandoc(path)
    except FileNotFoundError:
        raise RuntimeError(
            "未找到可用的提取工具。请安装其中一种:\n"
            "  pip install ebooklib beautifulsoup4\n"
            "  或  apt install pandoc"
        )


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("用法: python extract_epub.py <file.epub>")
        sys.exit(1)
    print(extract_epub(sys.argv[1]))