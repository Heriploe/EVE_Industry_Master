import sys as _sys_patch
_sys_patch.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent.parent))
from utilities.io.csv_reader import read_item_list
from utilities.data.app_config import load_app_config as _load_app_cfg, load_meta as _load_meta, resolve as _resolve_path
_cfg_cache = [None, None]
def _get_eve_cfg():
    if _cfg_cache[0] is None:
        _cfg_cache[0], _cfg_cache[1] = _load_app_cfg()
    return _cfg_cache[0], _cfg_cache[1]

"""
split_item_to_sell.py — 对比 Jita 与建筑市场售价，按较高价分类输出。

所有选项均从 config.ini [sell_tools] 读取，无命令行参数（除输入文件路径外）。

输入：  item_list.csv
输出：  item_to_sell_Jita.csv / item_to_sell_4H.csv

config.ini [sell_tools] 相关字段：
  check_volume   = false          是否启用成交量过滤
  volume_file    = Cache/Market/price_materials_all.json
  min_volume     = 1              低于此 vale 成交量强制选 Jita
"""

import csv
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


from utilities.market.order_utils import (
    fetch_region_prices, get_structure_prices_cached,
    need_structure_fetch, get_structure_token, find_type_id,
)




def write_csv(path: str, rows: List[Tuple]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        for row in rows:
            writer.writerow(list(row))


def load_volume_index(json_path: str) -> Dict[int, float]:
    idx: Dict[int, float] = {}
    try:
        with open(json_path, encoding="utf-8") as f:
            for e in json.load(f):
                tid = e.get("id")
                vol = e.get("vale_of_the_silent", {}).get("volume", 0.0) or 0.0
                if tid is not None:
                    idx[int(tid)] = float(vol)
        print(f"[split_item_to_sell] 成交量数据: {len(idx)} 条目")
    except Exception as e:
        print(f"[split_item_to_sell] 成交量加载失败: {e}")
    return idx


def decide(r_price, s_price, struct_label, *,
           check_volume=False, vale_vol=0.0, min_vol=1.0):
    if r_price is None and s_price is None:
        return None, "✗ 无数据"
    if r_price is None:
        return struct_label, f"→ {struct_label}"
    if s_price is None:
        return "Jita", "→ Jita"
    if s_price >= r_price:
        if check_volume and vale_vol < min_vol:
            return "Jita", f"→ Jita（4-H 量={vale_vol:.1f}<{min_vol:.0f}）"
        return struct_label, f"→ {struct_label}"
    return "Jita", "→ Jita"


def main():
    _cfg, _root = _get_eve_cfg()
    _meta = _load_meta(_root)
    _st   = _cfg.get("sell_tools", {})
    _res  = _cfg.get("resources", {})
    region_id    = int(_st.get("region_id",     10000002))
    station_id   = int(_st.get("station_id",    60003760))
    timeout      = 10.0
    ttl_hours    = float(_st.get("cache_ttl_hours", 24.0))
    s_cache      = str(_resolve_path(_root, _res.get("structure_orders", "resources/market/structure_cache.json")))
    s_id         = int(_st.get("structure_id",  1053654548169))
    struct_label = _st.get("structure_label",   "4-HWWF")
    types_json   = str(_resolve_path(_root, _cfg["data"]["types"]))
    out_dir      = str(_resolve_path(_root, _cfg.get("output_dir", "outputs/market_analyzer")))
    check_volume = bool(_st.get("check_volume", False))
    volume_file  = str(_resolve_path(_root, _res.get("price_all", "resources/market/price_all.json")))
    min_volume   = float(_st.get("min_volume",  1.0))

    item_list_path = str(_resolve_path(_root, _meta.get("inputs", {}).get("item_list", "inputs/item_list.csv")))
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        item_list_path = sys.argv[1]

    if not os.path.isfile(item_list_path):
        print(f"[Error] 找不到: {item_list_path}"); sys.exit(1)

    items = read_item_list(item_list_path)
    print(f"[split_item_to_sell] {len(items)} 个物品")

    vol_idx: Dict[int, float] = {}
    if check_volume:
        vol_idx = load_volume_index(volume_file)
        print(f"  check_volume=true，min_volume={min_volume}")

    resolved: List[Tuple[str, int, int]] = []
    for name, qty in items:
        tid = find_type_id(name, types_json)
        if tid:
            resolved.append((name, qty, tid))
        else:
            print(f"  ✗ 未找到 typeID: {name!r}")

    if not resolved:
        print("[Error] 没有找到任何 typeID"); sys.exit(1)

    type_ids = [tid for _, _, tid in resolved]

    print(f"\n[split_item_to_sell] 查询 Jita region={region_id}...")
    region_prices = fetch_region_prices(type_ids, region_id, station_id, "sell", timeout)

    print(f"\n[split_item_to_sell] 查询建筑 id={s_id}...")
    token = None
    if need_structure_fetch(s_cache, ttl_hours):
        token = get_structure_token(_meta, _root)
        if not token:
            print("[Error] 无法获取 ESI token"); sys.exit(1)
    structure_prices = get_structure_prices_cached(s_id, token, s_cache, ttl_hours, timeout)

    jita_rows, h4_rows, no_data = [], [], []

    print(f"\n{'物品名称':35s} {'Jita':>18s} {struct_label:>18s}  →")
    print("─" * 80)

    for name, qty, tid in resolved:
        r = region_prices.get(str(tid))
        s = structure_prices.get(str(tid))
        vv = vol_idx.get(tid, 0.0) if check_volume else 0.0

        dest, direction = decide(r, s, struct_label,
                                 check_volume=check_volume,
                                 vale_vol=vv, min_vol=min_volume)

        r_str = f"{r:>,.2f}" if r is not None else "    无数据"
        s_str = f"{s:>,.2f}" if s is not None else "    无数据"
        print(f"{name[:35]:35s} {r_str:>18s} {s_str:>18s}  {direction}")

        if dest is None:
            no_data.append((name, qty))
        elif dest == "Jita":
            jita_rows.append((name, qty))
        else:
            h4_rows.append((name, qty))

    print("─" * 80)
    print(f"Jita: {len(jita_rows)}   {struct_label}: {len(h4_rows)}   无数据: {len(no_data)}")

    write_csv(os.path.join(out_dir, "item_to_sell_Jita.csv"), jita_rows)
    write_csv(os.path.join(out_dir, "item_to_sell_4H.csv"),   h4_rows)
    print(f"\n已写入 → {out_dir}/item_to_sell_{{Jita,4H}}.csv")


if __name__ == "__main__":
    main()
