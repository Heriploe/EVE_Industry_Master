"""
filter_execution_list.py
========================
按 final_products 总价值过滤，输出 final_products_filtered。

修复：
  - load_prices 改为基于 type_id 匹配，而非名称字符串匹配，
    避免因名称拼写差异导致所有物品被静默过滤掉。
  - 使用 config_utils.REPO_ROOT
"""

import argparse
import csv
import json
from pathlib import Path
from typing import List, Tuple

from Utilities.config_utils import REPO_ROOT
from Utilities.blueprint_utils import build_prices, get_price
from Utilities.name_mapping import load_types_map, name_to_id

DEFAULT_JITA_PRICES  = "Cache/Market/price_materials_all.json"
DEFAULT_FINAL_PRODUCTS = "Cache/Output/final_products.csv"
DEFAULT_OUTPUT       = "Cache/Output/final_products_filtered.csv"
DEFAULT_MIN_VALUE    = 25_000_000


def resolve_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else REPO_ROOT / p


def load_config():
    import configparser
    config = configparser.ConfigParser()
    config.read(REPO_ROOT / "config.ini", encoding="utf-8")
    return config


def parse_line(line: str) -> Tuple[str, float]:
    raw = line.strip()
    if not raw:
        return "", 0.0
    if "\t" in raw:
        name, qty = raw.rsplit("\t", 1)
    elif "," in raw:
        name, qty = raw.rsplit(",", 1)
    else:
        return raw, 0.0
    try:
        quantity = float(qty.strip())
    except ValueError:
        quantity = 0.0
    return name.strip(), quantity


def load_simple_rows(path: Path) -> List[str]:
    with path.open("r", encoding="utf-8-sig") as f:
        return [line.rstrip("\n") for line in f if line.strip()]


def load_prices_by_id(path: Path) -> dict:
    """
    修复：返回 {type_id: jita_buy_price} 字典，基于 type_id 匹配。
    原实现用名称字符串匹配，任何拼写差异都会导致静默价格归零。
    """
    data = json.load(path.open("r", encoding="utf-8"))
    return build_prices(data)


def main():
    config = load_config()
    default_jita  = config.get("calculator", "jita_prices_json",  fallback=DEFAULT_JITA_PRICES)
    default_final = str(
        (resolve_path(config.get("calculator", "output_dir", fallback="Cache/Output")) / "final_products.csv")
        .relative_to(REPO_ROOT)
    )

    parser = argparse.ArgumentParser(description="按 final_products 总价值过滤并输出 final_products_filtered")
    parser.add_argument("--jita-prices",     default=default_jita,          help="jita_prices.json 路径")
    parser.add_argument("--final-products",  default=default_final,         help="final_products.csv 路径")
    parser.add_argument("--types-json",      default="Data/types.json",     help="types.json 路径（用于名称→ID映射）")
    parser.add_argument("--output",          default=DEFAULT_OUTPUT,        help="过滤后输出路径")
    parser.add_argument("--min-total-value", type=float, default=DEFAULT_MIN_VALUE, help="最小总价值阈值")
    args = parser.parse_args()

    jita_path   = resolve_path(args.jita_prices)
    final_path  = resolve_path(args.final_products)
    types_path  = resolve_path(args.types_json)
    output_path = resolve_path(args.output)

    if not jita_path.exists():
        raise FileNotFoundError(f"未找到 jita_prices.json: {jita_path}")
    if not final_path.exists():
        raise FileNotFoundError(f"未找到 final_products.csv: {final_path}")

    # 修复：用 type_id 查价格，用 name→id 映射中转
    prices    = load_prices_by_id(jita_path)
    types_map = load_types_map(types_path)
    name2id   = name_to_id(types_map)

    final_rows = load_simple_rows(final_path)
    kept    = []
    dropped = 0
    for final_line in final_rows:
        product_name, quantity = parse_line(final_line)
        if not product_name:
            continue
        type_id    = name2id.get(product_name)
        unit_price = get_price(prices, type_id, region_key="jita", field="buy") if type_id is not None else 0.0
        total_value = quantity * unit_price

        if total_value >= float(args.min_total_value):
            kept.append(final_line)
        else:
            dropped += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        for line in kept:
            if "\t" in line:
                left, right = line.rsplit("\t", 1)
            elif "," in line:
                left, right = line.rsplit(",", 1)
            else:
                left, right = line, ""
            writer.writerow([left.strip(), right.strip()])

    print(json.dumps({
        "final_products": len(final_rows),
        "kept": len(kept),
        "dropped": dropped,
        "min_total_value": float(args.min_total_value),
        "output": str(output_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
