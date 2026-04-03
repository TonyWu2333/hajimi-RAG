"""
extract_excel.py
提取 Excel 文件（.xlsx / .xlsm / .xls / .ods）中的纯文本。

依赖:
    pip install openpyxl xlrd odfpy
    xlrd  ── 读取 .xls
    odfpy ── 读取 .ods

用法:
    from extract_excel import extract_excel
    text = extract_excel("path/to/file.xlsx")
"""

from pathlib import Path


def extract_excel(file_path: str, sheet_separator: str = "\n\n") -> str:
    """
    提取 Excel / 电子表格文件的文本内容。

    逐工作表、逐行读取，单元格值用制表符拼接，行间换行，
    工作表之间用 sheet_separator 分隔，并在每张表前加表名标题。

    Args:
        file_path: 电子表格文件路径。
        sheet_separator: 工作表之间的分隔符（默认两个换行）。

    Returns:
        提取的纯文本字符串。

    Raises:
        FileNotFoundError: 文件不存在。
        ImportError: 缺少所需依赖。
        ValueError: 不支持的文件格式。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    suffix = path.suffix.lower()
    supported = {".xlsx", ".xlsm", ".xls", ".ods"}
    if suffix not in supported:
        raise ValueError(f"不支持的格式: {suffix}，支持 {supported}")

    import pandas as pd

    # 选择读取引擎
    engine_map = {
        ".xlsx": "openpyxl",
        ".xlsm": "openpyxl",
        ".xls": "xlrd",
        ".ods": "odf",
    }
    engine = engine_map[suffix]

    try:
        sheets: dict = pd.read_excel(
            str(path), sheet_name=None, engine=engine, dtype=str, header=None
        )
    except ImportError as e:
        pkg = {
            "openpyxl": "openpyxl",
            "xlrd": "xlrd",
            "odf": "odfpy",
        }[engine]
        raise ImportError(f"请先安装依赖: pip install {pkg}") from e

    sheet_parts = []
    for sheet_name, df in sheets.items():
        rows = []
        rows.append(f"=== {sheet_name} ===")
        for _, row in df.iterrows():
            # 过滤全 NaN 行；将 NaN 转为空字符串
            cells = [
                "" if (val != val or str(val) == "nan") else str(val)
                for val in row
            ]
            if any(c.strip() for c in cells):
                rows.append("\t".join(cells))
        sheet_parts.append("\n".join(rows))

    return sheet_separator.join(sheet_parts)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("用法: python extract_excel.py <file.xlsx>")
        sys.exit(1)
    print(extract_excel(sys.argv[1]))