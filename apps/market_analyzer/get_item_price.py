"""
get_item_price.py  ─  根据 item_list.csv 查询物品价格
支持 region（Jita）和 structure（建筑市场）两种价格来源。

用法:
  python get_item_price.py
  python get_item_price.py /path/to/item_list.csv
"""

# ── Imports ────────────────────────────────────────────────────────────────────
import csv
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_APP_ROOT.parent.parent))

from utilities.data.app_config import load_app_config, load_meta, resolve
from utilities.io.csv_reader import read_item_list
from utilities.market.order_utils import (
    fetch_region_prices,
    find_type_id,
    get_structure_prices_cached,
    get_structure_token,
    need_structure_fetch,
)


# ── Config ─────────────────────────────────────────────────────────────────────
def load_settings():
    cfg, eve_root = load_app_config()
    meta = load_meta(eve_root)
    return cfg, meta, eve_root


# ── Output ─────────────────────────────────────────────────────────────────────
def write_sell_export(path: str, rows: List[Tuple[str, float]]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        for name, price in rows:
            writer.writerow([name, price])


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    cfg, meta, eve_root = load_settings()
    st  = cfg.get("sell_tools", {})
    res = cfg.get("resources", {})

    # 价格来源配置
    price_source  = st.get("price_source",    "structure")
    discount      = float(st.get("discount",  0.0))
    region_id     = int(st.get("region_id",   10000002))
    station_id    = int(st.get("station_id",  60003760))
    structure_id  = int(st.get("structure_id", 1053654548169))
    ttl_hours     = float(st.get("cache_ttl_hours", 24.0))
    order_type    = st.get("order_type", "sell")
    check_volume  = bool(st.get("check_volume", True))
    min_volume    = float(st.get("min_volume", 1.0))
    timeout       = 10.0

    structure_cache = str(resolve(eve_root, res.get(
        "structure_orders", "resources/market/structure_cache.json")))
    types_json  = str(resolve(eve_root, cfg["data"]["types"]))
    out_dir     = str(resolve(eve_root, cfg.get("output_dir", "outputs/market_analyzer")))
    out_file    = st.get("output_file", "sell_export.csv")
    item_list_path = str(resolve(
        eve_root, meta.get("inputs", {}).get("item_list", "inputs/item_list.csv")))

    # 可选：命令行传入 item_list 路径
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        item_list_path = sys.argv[1]

    print(f"[get_item_price] source={price_source}  discount={discount:.1%}  check_volume={check_volume}")

    # ── 读取物品列表 ──────────────────────────────────────────────────────────
    if not os.path.isfile(item_list_path):
        print(f"[错误] 找不到物品列表: {item_list_path}")
        sys.exit(1)

    items = read_item_list(item_list_path)
    print(f"[get_item_price] {len(items)} 个物品: {item_list_path}")

    resolved: List[Tuple[str, int]] = []
    for name, _qty in items:
        tid = find_type_id(name, types_json)
        if tid:
            resolved.append((name, tid))
        else:
            print(f"  ✗ 未找到 typeID: {name!r}")

    if not resolved:
        print("[错误] 没有找到任何 typeID")
        sys.exit(1)

    type_ids = [tid for _, tid in resolved]
    prices: dict = {}

    # ── 查询价格 ──────────────────────────────────────────────────────────────
    if price_source == "region":
        prices = fetch_region_prices(type_ids, region_id, station_id, order_type, timeout)

    elif price_source == "structure":
        token: Optional[str] = None
        if need_structure_fetch(structure_cache, ttl_hours):
            token = get_structure_token(meta, eve_root)
            if not token:
                print("[错误] 无法获取 ESI token，请检查 config_meta.json 认证配置")
                sys.exit(1)
        prices = get_structure_prices_cached(
            structure_id, token, structure_cache, ttl_hours, timeout)
        print(f"  structure_id={structure_id}")

    else:
        print(f"[错误] 不支持的 price_source: {price_source!r}（可选: region / structure）")
        sys.exit(1)

    # ── 格式化输出 ────────────────────────────────────────────────────────────
    out_rows: List[Tuple[str, float]] = []
    col_w = 40
    discount_col = discount > 0
    print()
    print(f"{'物品名称':{col_w}s} {'市场价':>18s}" + (f" {'调整后':>18s}" if discount_col else ""))
    print("─" * (col_w + 20 + (20 if discount_col else 0)))

    for name, tid in resolved:
        raw_price = prices.get(str(tid)) or prices.get(tid)
        if raw_price is None:
            print(f"{name[:col_w]:{col_w}s} {'无数据':>18s}")
            continue
        adjusted = raw_price * (1.0 - discount) if discount_col else raw_price
        out_rows.append((name, adjusted))
        adj_str = f" {adjusted:>18,.2f}" if discount_col else ""
        print(f"{name[:col_w]:{col_w}s} {raw_price:>18,.2f}{adj_str}")

    if not out_rows:
        print("没有获取到任何价格")
        sys.exit(0)

    out_path = os.path.join(out_dir, out_file)
    write_sell_export(out_path, out_rows)
    print(f"\n[get_item_price] 已写入 {len(out_rows)} 行 → {out_path}")


if __name__ == "__main__":
    main()
