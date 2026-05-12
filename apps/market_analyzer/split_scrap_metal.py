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
split_scrap_metal.py — 对比原物品与拆解物料的 Jita buy 价值，判断是否值得拆解。

所有选项均从 config.ini [sell_tools] 读取，无命令行参数（除输入文件路径外）。

config.ini [sell_tools] 相关字段：
  scrap_threshold       = 0.80   拆解价值阈值（默认 80%）
  scrap_outfile         = items_to_scrap.csv
  scrap_materials_file  = Data/typeMaterials.yaml
  scrap_cache_file      = Cache/Market/scrap_price_cache.json
  scrap_refresh_cache   = false  true = 忽略缓存重新查询
"""

import csv
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


from utilities.market.order_utils import fetch_best_buy, find_type_id




def load_type_materials(yaml_path: str) -> Dict[int, List[Dict]]:
    try:
        import yaml
    except ImportError:
        print("[Error] 需要 pyyaml：pip install pyyaml"); sys.exit(1)
    with open(yaml_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    result = {}
    for type_id, entry in raw.items():
        mats = (entry or {}).get("materials")
        if mats:
            result[int(type_id)] = mats
    return result


def load_cache(path: str) -> Dict[str, float]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return {str(k): float(v) for k, v in json.load(f).items()}
    except Exception as e:
        print(f"[split_scrap_metal] 缓存读取失败({e})"); return {}


def save_cache(path: str, cache: Dict[str, float]) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[split_scrap_metal] 缓存写入失败: {e}")


def resolve_material_prices(
    mat_ids: List[int], cache: Dict[str, float],
    region_id: int, station_id: int, timeout: float,
    refresh: bool = False,
) -> Dict[str, float]:
    needed = [m for m in mat_ids if refresh or str(m) not in cache]
    if not needed:
        print(f"[split_scrap_metal] 所有 {len(mat_ids)} 种物料已在缓存中")
        return cache
    print(f"[split_scrap_metal] 需查询 {len(needed)}/{len(mat_ids)} 种物料...")
    for i, tid in enumerate(needed, 1):
        p = fetch_best_buy(tid, region_id, station_id, timeout)
        if p is not None:
            cache[str(tid)] = p
            print(f"  [{i}/{len(needed)}] {tid}  buy={p:,.2f}")
        else:
            print(f"  [{i}/{len(needed)}] {tid}  无数据")
    return cache


def write_scrap_csv(path: str, rows: List[Tuple]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["物品名称", "数量", "原物品buy价", "拆解物料总价", "价值比%"])
        for name, qty, item_buy, scrap_sum, ratio_pct in rows:
            writer.writerow([name, qty, f"{item_buy:.2f}", f"{scrap_sum:.2f}", f"{ratio_pct:.2f}"])


def main():
    _cfg, _root = _get_eve_cfg()
    _meta = _load_meta(_root)
    _st = _cfg.get("sell_tools", {})

    region_id     = int(_st.get("region_id",  10000002))
    station_id    = int(_st.get("station_id", 60003760))
    timeout       = 10.0
    types_json    = str(_resolve_path(_root, _cfg["data"]["types"]))
    out_dir       = str(_resolve_path(_root, _cfg.get("output_dir", "outputs/market_analyzer")))
    threshold     = float(_st.get("scrap_threshold", 0.80))
    out_file      = _st.get("scrap_outfile", "items_to_scrap.csv")
    mat_file      = str(_resolve_path(_root, "data/typeMaterials.yaml"))
    cache_file    = str(_resolve_path(_root, "resources/market/scrap_price_cache.json"))
    refresh_cache = bool(_st.get("scrap_refresh_cache", False))

    item_list_path = str(_resolve_path(_root, _meta.get("inputs", {}).get("item_list", "inputs/item_list.csv")))
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        item_list_path = sys.argv[1]

    print(f"[split_scrap_metal] threshold={threshold:.0%}  refresh_cache={refresh_cache}")

    for p in [item_list_path, mat_file]:
        if not os.path.isfile(p):
            print(f"[Error] 找不到: {p}"); sys.exit(1)

    items     = read_item_list(item_list_path)
    type_mats = load_type_materials(mat_file)
    print(f"[split_scrap_metal] {len(items)} 个物品，{len(type_mats)} 条拆解配方")

    resolved: List[Tuple[str, int, int]] = []
    no_mat: List[str] = []
    for name, qty in items:
        tid = find_type_id(name, types_json)
        if tid is None:
            print(f"  ✗ 未找到 typeID: {name!r}"); continue
        if tid not in type_mats:
            print(f"  ✗ 无拆解配方: {name!r}"); no_mat.append(name); continue
        resolved.append((name, qty, tid))
        print(f"  ✓ {name} → {tid}（{len(type_mats[tid])} 种物料）")

    if not resolved:
        print("[Error] 没有可处理的物品"); sys.exit(1)

    all_mat_ids = list({m["materialTypeID"]
                        for _, _, tid in resolved
                        for m in type_mats[tid]})

    mat_cache = load_cache(cache_file)
    mat_cache = resolve_material_prices(all_mat_ids, mat_cache,
                                        region_id, station_id, timeout,
                                        refresh=refresh_cache)
    save_cache(cache_file, mat_cache)
    print(f"[split_scrap_metal] 物料缓存已保存: {cache_file}")

    print(f"\n[split_scrap_metal] 查询原物品 buy 最高价...")
    item_buys: Dict[int, Optional[float]] = {}
    for name, _, tid in resolved:
        p = fetch_best_buy(tid, region_id, station_id, timeout)
        item_buys[tid] = p
        print(f"  {name!r}  buy={p:,.2f}" if p else f"  {name!r}  无数据")

    scrap_rows, skip_rows, nodata = [], [], []
    col_w = 34
    print(f"\n{'物品名称':{col_w}s} {'原buy价':>16s} {'物料总价':>16s} {'比率':>8s}  →")
    print("─" * (col_w + 48))

    for name, qty, tid in resolved:
        item_buy = item_buys.get(tid)
        scrap_sum = 0.0
        ok = True
        for m in type_mats[tid]:
            mp = mat_cache.get(str(m["materialTypeID"]))
            if mp is None:
                ok = False; break
            scrap_sum += mp * m["quantity"]

        if item_buy is None or not ok:
            nodata.append(name)
            print(f"{name[:col_w]:{col_w}s} {'数据缺失':>16s} {'':>16s} {'N/A':>8s}  ✗")
            continue

        ratio     = scrap_sum / item_buy
        ratio_pct = ratio * 100
        direction = "→ 拆解" if ratio >= threshold else "  跳过"
        if ratio >= threshold:
            scrap_rows.append((name, qty, item_buy, scrap_sum, ratio_pct))
        else:
            skip_rows.append(name)

        print(f"{name[:col_w]:{col_w}s} {item_buy:>16,.2f} {scrap_sum:>16,.2f} "
              f"{ratio_pct:>7.1f}%  {direction}")

    print("─" * (col_w + 48))
    print(f"拆解: {len(scrap_rows)}   跳过: {len(skip_rows)}   "
          f"数据缺失: {len(nodata)}   无配方: {len(no_mat)}")

    out_path = os.path.join(out_dir, out_file)
    if scrap_rows:
        write_scrap_csv(out_path, scrap_rows)
        print(f"\n已写入 {len(scrap_rows)} 行 → {out_path}")
    else:
        print("\n没有符合阈值的物品，未写入文件")


if __name__ == "__main__":
    main()
