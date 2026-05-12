"""
utilities/io/loaders.py
=======================
共享 I/O 工具：JSON / YAML / CSV 加载，供所有应用统一使用。
"""

import csv
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def load_json(path: Path) -> any:
    path = Path(path)
    log.debug("load_json: %s", path)
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: any, indent: int = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    log.debug("save_json: %s", path)


def load_yaml(path: Path) -> dict:
    import yaml
    path = Path(path)
    log.debug("load_yaml: %s", path)
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_csv_tsv(path: Path) -> list[dict]:
    """
    加载 CSV 或 TSV 文件，自动检测分隔符。
    支持 UTF-8-BOM。返回 [{"name": str, "qty": int}, ...] 格式。
    """
    path = Path(path)
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    # 检测分隔符
    delim = "\t" if "\t" in lines[0] else ","
    rows = []
    reader = csv.DictReader(lines, delimiter=delim)
    if reader.fieldnames and len(reader.fieldnames) >= 2:
        for row in reader:
            keys = list(row.keys())
            try:
                rows.append({"name": row[keys[0]].strip(), "qty": int(row[keys[1]])})
            except (ValueError, KeyError):
                continue
    else:
        # 无表头：按位置读
        for line in lines:
            parts = line.split(delim)
            if len(parts) >= 2:
                try:
                    rows.append({"name": parts[0].strip(), "qty": int(parts[1].strip())})
                except ValueError:
                    continue
    return rows
