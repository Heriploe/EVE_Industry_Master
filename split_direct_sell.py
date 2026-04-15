"""
split_direct_sell.py — 对比 Jita sell/buy 价差，筛选适合直接出售的物品。

所有选项均从 config.ini [sell_tools] 读取，无命令行参数（除输入文件路径外）。

当 (min_sell - max_buy) / max_buy < threshold，写入 direct_sell.csv。

config.ini [sell_tools] 相关字段：
  direct_sell_threshold = 0.05   价差阈值（默认 5%）
  direct_sell_outfile   = direct_sell.csv
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

from market_order_utils import fetch_sell_and_buy, find_type_id


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


def write_direct_sell(path: str, rows: List[Tuple]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["物品名称", "数量", "buy最高价", "价差%"])
        for name, qty, best_buy, spread_pct in rows:
            writer.writerow([name, qty, f"{best_buy:.2f}", f"{spread_pct:.2f}"])


def main():
    config = configparser.ConfigParser()
    config.read(_REPO / "config.ini", encoding="utf-8")

    region_id  = config.getint("market",     "region_id",              fallback=10000002)
    station_id = config.getint("sell_tools", "station_id",             fallback=60003760)
    timeout    = 10.0
    types_json = str(_REPO / config.get("paths","types_json",          fallback="Data/types.json"))
    out_dir    = str(_REPO / config.get("sell_tools","output_dir",     fallback="Cache/Output"))
    threshold  = config.getfloat("sell_tools","direct_sell_threshold", fallback=0.05)
    out_file   = config.get("sell_tools","direct_sell_outfile",        fallback="direct_sell.csv")

    item_list_path = str(_REPO / "item_list.csv")
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        item_list_path = sys.argv[1]

    if not os.path.isfile(item_list_path):
        print(f"[Error] 找不到: {item_list_path}"); sys.exit(1)

    items = load_item_list(item_list_path)
    print(f"[split_direct_sell] {len(items)} 个物品，threshold={threshold:.1%}")

    resolved: List[Tuple[str, int, int]] = []
    for name, qty in items:
        tid = find_type_id(name, types_json)
        if tid:
            resolved.append((name, qty, tid))
        else:
            print(f"  ✗ 未找到 typeID: {name!r}")

    if not resolved:
        print("[Error] 没有找到任何 typeID"); sys.exit(1)

    direct_rows, skip_rows, nodata = [], [], []

    col_w = 34
    print(f"\n{'物品名称':{col_w}s} {'sell最低':>14s} {'buy最高':>14s} {'价差%':>8s}  →")
    print("─" * (col_w + 40))

    for name, qty, tid in resolved:
        best_sell, best_buy = fetch_sell_and_buy(tid, region_id, station_id, timeout)

        sell_s = f"{best_sell:>14,.2f}" if best_sell is not None else f"{'无数据':>14s}"
        buy_s  = f"{best_buy:>14,.2f}"  if best_buy  is not None else f"{'无数据':>14s}"

        if best_sell is None or best_buy is None:
            nodata.append(name)
            print(f"{name[:col_w]:{col_w}s} {sell_s} {buy_s} {'N/A':>8s}  ✗ 数据缺失")
            continue

        spread = (best_sell - best_buy) / best_buy
        spread_pct = spread * 100
        spread_s = f"{spread_pct:>7.2f}%"

        if spread < threshold:
            direct_rows.append((name, qty, best_buy, spread_pct))
            direction = "→ direct_sell"
        else:
            skip_rows.append(name)
            direction = "  跳过"

        print(f"{name[:col_w]:{col_w}s} {sell_s} {buy_s} {spread_s}  {direction}")

    print("─" * (col_w + 40))
    print(f"direct_sell: {len(direct_rows)}   跳过: {len(skip_rows)}   数据缺失: {len(nodata)}")

    out_path = os.path.join(out_dir, out_file)
    if direct_rows:
        write_direct_sell(out_path, direct_rows)
        print(f"\n已写入 {len(direct_rows)} 行 → {out_path}")
    else:
        print("\n没有符合阈值的物品，未写入文件")


if __name__ == "__main__":
    main()
