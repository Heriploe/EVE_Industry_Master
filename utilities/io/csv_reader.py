"""
utilities/io/csv_reader.py
==========================
稳健的 CSV / TSV 读取器，统一供所有 app 使用。

支持：
  - 自动检测分隔符（\t 或 ,）
  - UTF-8-BOM 编码
  - 空行 / 注释行（# 开头）跳过
  - 行内多余空格裁剪
  - 多列格式（名称 + 数量 + 可选附加列）
  - 数量字段含千位逗号（"1,000,000" → 1000000）
  - 无表头 / 有表头两种模式
"""

import csv
import io
from pathlib import Path
from typing import Optional


def _detect_delimiter(sample: str) -> str:
    """从首行样本中检测分隔符（优先 tab，其次逗号）"""
    if '\t' in sample:
        return '\t'
    return ','


def _clean_int(s: str) -> Optional[int]:
    """将字符串转换为整数，支持千位逗号，失败返回 None"""
    try:
        return int(s.strip().replace(',', ''))
    except (ValueError, AttributeError):
        return None


def _clean_float(s: str) -> Optional[float]:
    """将字符串转换为浮点数，支持千位逗号，失败返回 None"""
    try:
        return float(s.strip().replace(',', ''))
    except (ValueError, AttributeError):
        return None


def read_name_qty(
    path: Path,
    name_col: int = 0,
    qty_col: int = 1,
    skip_header: bool = False,
    default_qty: int = 1,
) -> list[dict]:
    """
    读取「名称 + 数量」格式的 CSV/TSV 文件。

    返回：[{"name": str, "qty": int}, ...]

    参数：
        path        - 文件路径
        name_col    - 名称所在列索引（默认 0）
        qty_col     - 数量所在列索引（默认 1）
        skip_header - 跳过首行（表头）
        default_qty - 数量列缺失或无效时的默认值
    """
    path = Path(path)
    if not path.exists():
        return []

    raw = path.read_bytes().decode('utf-8-sig')  # 自动处理 BOM
    lines = [l for l in raw.splitlines() if l.strip() and not l.strip().startswith('#')]
    if not lines:
        return []

    delim = _detect_delimiter(lines[0])
    reader = csv.reader(io.StringIO('\n'.join(lines)), delimiter=delim)

    rows = list(reader)
    if skip_header and rows:
        rows = rows[1:]

    result = []
    for row in rows:
        if not row:
            continue
        cols = [c.strip() for c in row]

        name = cols[name_col].strip() if name_col < len(cols) else ''
        if not name:
            continue

        if qty_col < len(cols) and cols[qty_col]:
            qty = _clean_int(cols[qty_col]) or default_qty
        else:
            qty = default_qty

        result.append({"name": name, "qty": qty})

    return result


def read_tsv_rows(
    path: Path,
    skip_header: bool = False,
    min_cols: int = 1,
) -> list[list[str]]:
    """
    读取 CSV/TSV 文件，返回所有行的列列表（已裁剪空格、跳过空行和注释行）。

    返回：[["col1", "col2", ...], ...]
    """
    path = Path(path)
    if not path.exists():
        return []

    raw = path.read_bytes().decode('utf-8-sig')
    lines = [l for l in raw.splitlines() if l.strip() and not l.strip().startswith('#')]
    if not lines:
        return []

    delim = _detect_delimiter(lines[0])
    reader = csv.reader(io.StringIO('\n'.join(lines)), delimiter=delim)
    rows = list(reader)
    if skip_header and rows:
        rows = rows[1:]

    return [
        [c.strip() for c in row]
        for row in rows
        if len(row) >= min_cols
    ]


def read_provider(path: Path) -> dict:
    """
    读取供应商表，返回 {ore_name: {"price": float, "max_qty": int}}

    CSV 格式：名称, 单价, 最大数量
    """
    result = {}
    for row in read_tsv_rows(path, min_cols=3):
        name = row[0]
        price = _clean_float(row[1])
        max_qty = _clean_int(row[2])
        if name and price is not None and max_qty is not None:
            result[name] = {"price": price, "max_qty": max_qty}
    return result


def read_purchase_list(path: Path) -> dict:
    """
    读取采购清单，返回 {mineral_name: qty}

    支持格式：
      - TAB 分隔：名称<TAB>数量
      - 逗号分隔：名称,数量
      - 数量含千位逗号："1,000,000"
    """
    result = {}
    for entry in read_name_qty(path):
        result[entry["name"]] = entry["qty"]
    return result


def read_item_list(path: Path) -> list[tuple[str, int]]:
    """
    读取物品清单，返回 [(name, qty), ...]
    用于 market_analyzer 的出售清单。
    """
    return [(e["name"], e["qty"]) for e in read_name_qty(path)]
