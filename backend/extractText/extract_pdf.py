"""
extract_pdf.py
提取 PDF 文件中的纯文本。

依赖:
    pip install pypdf

用法:
    from extract_pdf import extract_pdf
    text = extract_pdf("path/to/file.pdf")
"""

from pathlib import Path


def extract_pdf(file_path: str) -> str:
    """
    提取 PDF 文件的文本内容。

    Args:
        file_path: PDF 文件路径。

    Returns:
        所有页面拼接后的纯文本字符串。

    Raises:
        FileNotFoundError: 文件不存在。
        ImportError: 未安装 pypdf。
        ValueError: 文件不是有效的 PDF。
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError("请先安装依赖: pip install pypdf")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"不是 PDF 文件: {file_path}")

    reader = PdfReader(str(path))
    pages_text = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages_text.append(text)

    return "\n".join(pages_text)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("用法: python extract_pdf.py <file.pdf>")
        sys.exit(1)
    print(extract_pdf(sys.argv[1]))