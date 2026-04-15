"""
get_item_price.py — 根据 item_list.csv 查询价格，导出 sell_export.csv

所有选项均从 config.ini [sell_tools] 读取，无命令行参数（除输入文件路径外）。

用法：
  python get_item_price.py [item_list.csv]
"""

import configparser
import csv
import os
import sys
from pathlib import Path
from typing import List, Tuple, Optional

_REPO = next((p for p in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]
              if (p / "config.ini").exists()), Path(__file__).resolve().parent)
sys.path.insert(0, str(_REPO / "Utilities"))

from market_order_utils import (
    fetch_region_prices, get_structure_prices_cached,
    need_structure_fetch, get_structure_token, find_type_id,
)


def load_item_list(path: str) -> List[Tuple[str, int]]:
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for line in csv.reader(f, delimiter="\t"):
            if not line or line[0].startswith("#"):
                continue
            name = line[0].strip()
            qty  = int(line[1].strip()) if len(line) > 1 and line[1].strip().isdigit() else 1
            if name:
                rows.append((name, qty))
    return rows


def write_sell_export(path: str, rows: List[Tuple[str, float]]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        for name, price in rows:
            writer.writerow([name, price])


def main():
    config = configparser.ConfigParser()
    config.read(_REPO / "config.ini", encoding="utf-8")

    def _get(section, key, fallback=""):
        return config.get(section, key, fallback=fallback)
    def _getfloat(section, key, fallback=0.0):
        return config.getfloat(section, key, fallback=fallback)
    def _getint(section, key, fallback=0):
        return config.getint(section, key, fallback=fallback)

    price_source = _get("sell_tools", "price_source",  "region")
    discount     = _getfloat("sell_tools", "discount",  0.0)
    region_id    = _getint("market",    "region_id",    10000002)
    station_id   = _getint("sell_tools","station_id",   60003760)
    timeout      = 10.0
    ttl_hours    = _getfloat("sell_tools","cache_ttl_hours", 24.0)
    order_type   = _get("sell_tools","order_type", "sell")
    s_cache      = str(_REPO / _get("sell_tools","structure_cache_file",
                                    "Cache/Market/structure_cache.json"))
    s_id         = _getint("sell_tools","structure_id", 0)
    types_json   = str(_REPO / _get("paths","types_json", "Data/types.json"))
    out_dir      = str(_REPO / _get("sell_tools","output_dir", "Cache/Output"))
    out_file     = _get("sell_tools","output_file", "sell_export.csv")
    item_list_path = str(_REPO / "item_list.csv")

    # 支持直接传入 item_list 路径作为唯一位置参数
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        item_list_path = sys.argv[1]

    print(f"[get_item_price] price_source={price_source}  discount={discount:.1%}")
    print(f"[get_item_price] output: {os.path.join(out_dir, out_file)}")

    if not os.path.isfile(item_list_path):
        print(f"[Error] 找不到: {item_list_path}"); sys.exit(1)

    items = load_item_list(item_list_path)
    print(f"[get_item_price] {len(items)} 个物品: {item_list_path}")

    resolved: List[Tuple[str, int]] = []
    for name, _qty in items:
        tid = find_type_id(name, types_json)
        if tid:
            resolved.append((name, tid))
            print(f"  ✓ {name} → {tid}")
        else:
            print(f"  ✗ 未找到 typeID: {name!r}")

    if not resolved:
        print("[Error] 没有找到任何 typeID"); sys.exit(1)

    type_ids = [tid for _, tid in resolved]
    prices = {}

    if price_source == "region":
        prices = fetch_region_prices(type_ids, region_id, station_id, order_type, timeout)
    elif price_source == "structure":
        token = None
        if need_structure_fetch(s_cache, ttl_hours):
            token = get_structure_token(config, _REPO)
            if not token:
                print("[Error] 无法获取 ESI token"); sys.exit(1)
        prices = get_structure_prices_cached(s_id, token, s_cache, ttl_hours, timeout)
    else:
        print(f"[Error] 不支持的 price_source: {price_source!r}"); sys.exit(1)

    if discount > 0:
        print(f"[get_item_price] 下调比例: {discount:.1%}")

    out_rows = []
    w = 40
    header = f"{'物品名称':{w}s} {'市场价':>18s}" + (f" {'调整后':>18s}" if discount > 0 else "")
    print(f"\n{header}")
    print("─" * (w + 20 + (20 if discount > 0 else 0)))

    for name, tid in resolved:
        p = prices.get(str(tid))
        if p is None:
            print(f"{name[:w]:{w}s} {'无数据':>18s}")
        else:
            adjusted = p * (1.0 - discount) if discount > 0 else p
            out_rows.append((name, adjusted))
            adj_str = f" {adjusted:>18,.2f}" if discount > 0 else ""
            print(f"{name[:w]:{w}s} {p:>18,.2f}{adj_str}")

    if not out_rows:
        print("没有获取到任何价格"); sys.exit(0)

    out_path = os.path.join(out_dir, out_file)
    write_sell_export(out_path, out_rows)
    print(f"\n[get_item_price] 已写入 {len(out_rows)} 行 → {out_path}")


if __name__ == "__main__":
    main()
