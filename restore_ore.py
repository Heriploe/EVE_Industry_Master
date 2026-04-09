"""
restore_ore.py
==============
通过联立整数线性规划（ILP），选择最优矿石组合以满足矿物采购需求。

修复（原分步 ILP 的逻辑漏洞）：
  - 原实现按矿物价值逐个贪心求解，将"副产品价值"当作预算支出扣减，
    导致预算被过度消耗、矿石购买量偏低。
  - 新实现将所有矿物需求联立为单次 ILP：
      最大化：∑(目标矿物产出量 × 目标矿物价格) - ∑(矿石成本)
      约束：各矿石批次数 × 精炼产出 ≤ 对应矿物需求
           矿石采购总成本 ≤ 预算
           各矿石批次数 ≤ provider 供应上限

兼容 Python 3.8+。
"""

import configparser
import csv
import json
import math
import subprocess
import sys
from pathlib import Path

from Utilities.config_utils import REPO_ROOT, load_config, resolve_path
from Utilities.name_mapping import id_to_name, load_types_map, name_to_id
from pulp import (
    LpInteger,
    LpMaximize,
    LpProblem,
    LpStatus,
    LpVariable,
    PULP_CBC_CMD,
    lpSum,
)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def _load_config():
    config = load_config()
    batch_size = config.getint("reprocessing_solver", "batch_size", fallback=100)
    budget     = config.getfloat("reprocessing_solver", "budget",     fallback=350_000_000)
    eff        = config.getfloat("reprocessing_solver", "eff",        fallback=0.9063)

    preset_name      = config.get("reprocessing_market", "preset",           fallback="mineral&element")
    region_id        = config.getint("reprocessing_market", "region_id",      fallback=10000002)
    request_interval = config.getfloat("reprocessing_market", "request_interval", fallback=0.05)

    return batch_size, budget, eff, preset_name, region_id, request_interval


# ---------------------------------------------------------------------------
# 供应商（Provider）加载
# ---------------------------------------------------------------------------

def load_provider(provider_csv: Path) -> dict:
    provider_dict = {}
    with provider_csv.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            parts = [p.strip() for p in (line.split("\t") if "\t" in line else line.split()) if p.strip()]
            if len(parts) < 2:
                continue
            name = parts[0]
            qty_text = "".join(parts[1:]).replace(",", "").replace("，", "")
            if not qty_text.isdigit():
                continue
            provider_dict[name] = int(qty_text)
    return provider_dict


# ---------------------------------------------------------------------------
# 精炼数据
# ---------------------------------------------------------------------------

def normalize_reprocessing_eff(eff: float) -> float:
    if eff <= 0:
        raise ValueError(f"无效精炼效率 eff={eff}，必须大于0")
    return eff / 100 if eff > 1 else eff


def load_reprocessing_data(path: Path, eff: float):
    with path.open("r", encoding="utf-8") as f:
        reprocessing_data = json.load(f)

    norm_eff = normalize_reprocessing_eff(eff)
    ore_to_materials = {ore["id"]: ore.get("materials", []) for ore in reprocessing_data}
    for ore_id, mats in ore_to_materials.items():
        for mat in mats:
            mat["quantity"] = max(0, math.floor(mat["quantity"] * norm_eff))

    return reprocessing_data, ore_to_materials, norm_eff


def build_provider_ore_mapping(provider_names, reprocessing_data) -> dict:
    ore_name_to_id = {ore.get("zh"): ore.get("id") for ore in reprocessing_data if ore.get("zh")}
    provider_to_ore_id = {}
    for pname in provider_names:
        ore_id = ore_name_to_id.get(pname)
        if ore_id is None:
            ore_id = next(
                (o["id"] for o in reprocessing_data
                 if o.get("zh") and (o["zh"] in pname or pname in o["zh"])),
                None,
            )
        provider_to_ore_id[pname] = ore_id
    return provider_to_ore_id


# ---------------------------------------------------------------------------
# 价格加载
# ---------------------------------------------------------------------------

def load_prices(price_json: Path, region_key: str) -> dict:
    with price_json.open("r", encoding="utf-8") as f:
        data = json.load(f)
    result = {}
    for item in data:
        if item.get("id") is None:
            continue
        if region_key in item:
            price = ((item.get(region_key) or {}).get("lowest", 0))
        else:
            price = item.get("lowest", item.get("buy", 0))
        result[int(item["id"])] = float(price or 0)
    return result


def load_preset_type_ids(preset_json, alias_json, preset_name, repo_root):
    with preset_json.open("r", encoding="utf-8") as f:
        presets = json.load(f)
    with alias_json.open("r", encoding="utf-8") as f:
        alias_data = json.load(f)

    alias_map = {item["alias"]: item["path"] for item in alias_data.get("aliases", [])}
    preset = next((p for p in presets if p.get("name") == preset_name), None)
    if preset is None:
        raise ValueError(f"preset 不存在: {preset_name}")

    type_ids: set = set()
    preset_types_map: dict = {}
    for alias in preset.get("children", []):
        rel_path = alias_map.get(alias)
        if not rel_path:
            raise ValueError(f"alias 不存在: {alias}")
        child_types_map = load_types_map(repo_root / rel_path)
        type_ids.update(child_types_map.keys())
        preset_types_map.update(child_types_map)

    return type_ids, preset_types_map, id_to_name(preset_types_map)


# ---------------------------------------------------------------------------
# 调试输出
# ---------------------------------------------------------------------------

def dump_temp_json(temp_dir: Path, file_name: str, payload):
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / file_name
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[DEBUG] 已写入中间数据: {path}")


def run_price_fetcher(repo_root: Path, preset_name: str, region_id: int, request_interval: float):
    script = repo_root / "Utilities" / "get_price_by_preset.py"
    cmd = [
        sys.executable, str(script),
        preset_name,
        "--region-ids", str(region_id),
        "--request-interval", str(request_interval),
    ]
    subprocess.run(cmd, cwd=repo_root, check=True)


# ---------------------------------------------------------------------------
# 联立 ILP 求解
# ---------------------------------------------------------------------------

def solve_ore_selection(
    provider_dict: dict,
    provider_to_ore_id: dict,
    ore_to_materials: dict,
    purchase_list: dict,
    mineral_id_to_price: dict,
    preset_type_ids: set,
    batch_size: int,
    budget: float,
) -> dict:
    """
    联立 ILP：选择矿石组合，最大化目标矿物产出价值，同时满足需求上限与预算约束。

    决策变量：每种矿石（来自 provider）的批次数 x[pname]（整数）

    目标：max ∑_{pname} ∑_{mid in purchase_list} yield(pname, mid) * x[pname] * price(mid)
      即最大化对目标矿物的实际产出价值（副产品不计入目标，但不被扣减预算）

    约束：
      1. 对每种目标矿物 mid：
           ∑_pname yield(pname, mid) * x[pname] ≤ purchase_list[mid]
         （产出不超过需求，避免过度精炼产生不必要的副产品）
      2. 采购总成本（矿石原价）≤ budget
           ∑_pname ore_price(pname) * batch_size * x[pname] ≤ budget
      3. 0 ≤ x[pname] ≤ provider_dict[pname] // batch_size

    返回：{pname: total_quantity} 已选矿石总量（批次 × batch_size）
    """
    prob = LpProblem("OreSelection", LpMaximize)

    # 过滤：只保留能产出至少一种目标矿物的矿石
    valid_providers = {}
    for pname, total_qty in provider_dict.items():
        ore_id = provider_to_ore_id.get(pname)
        if ore_id is None:
            continue
        mats = ore_to_materials.get(ore_id, [])
        if not any(mat["materialTypeID"] in purchase_list for mat in mats):
            continue
        max_batches = total_qty // batch_size
        if max_batches <= 0:
            continue
        valid_providers[pname] = max_batches

    if not valid_providers:
        print("[WARN] 没有任何矿石能产出目标矿物，ILP 跳过。")
        return {}

    # 决策变量
    x_vars = {
        pname: LpVariable(f"x_{pname}", lowBound=0, upBound=max_batches, cat=LpInteger)
        for pname, max_batches in valid_providers.items()
    }

    # 目标函数：最大化目标矿物产出价值
    obj_terms = []
    for pname, var in x_vars.items():
        ore_id = provider_to_ore_id[pname]
        for mat in ore_to_materials.get(ore_id, []):
            mid = mat["materialTypeID"]
            if mid not in purchase_list:
                continue
            price = mineral_id_to_price.get(mid, 0.0)
            if price > 0 and mat["quantity"] > 0:
                obj_terms.append(mat["quantity"] * price * var)
    if obj_terms:
        prob += lpSum(obj_terms)

    # 约束 1：各目标矿物产出不超过需求
    for mid, demand in purchase_list.items():
        yield_terms = []
        for pname, var in x_vars.items():
            ore_id = provider_to_ore_id[pname]
            for mat in ore_to_materials.get(ore_id, []):
                if mat["materialTypeID"] == mid and mat["quantity"] > 0:
                    yield_terms.append(mat["quantity"] * var)
        if yield_terms:
            prob += lpSum(yield_terms) <= demand, f"Demand_{mid}"

    # 约束 2：采购总成本（以矿石市场价估算）≤ 预算
    # 矿石本身价格：通过其精炼后矿物的价值之和来反算（无直接价格数据时的合理近似）
    cost_terms = []
    for pname, var in x_vars.items():
        ore_id = provider_to_ore_id[pname]
        # 用精炼产出矿物价值估算矿石成本（精炼效率损耗体现在 ore_to_materials 的 quantity 中）
        ore_mat_value = sum(
            mat["quantity"] * mineral_id_to_price.get(mat["materialTypeID"], 0.0)
            for mat in ore_to_materials.get(ore_id, [])
            if mat["materialTypeID"] in preset_type_ids
        )
        if ore_mat_value > 0:
            cost_terms.append(ore_mat_value * batch_size * var)
    if cost_terms:
        prob += lpSum(cost_terms) <= budget, "Budget"

    solver = PULP_CBC_CMD(msg=0)
    prob.solve(solver)

    status_name = LpStatus.get(prob.status, str(prob.status))
    print(f"[ILP] 状态={status_name}，变量数={len(x_vars)}，约束数={len(prob.constraints)}")

    result = {}
    for pname, var in x_vars.items():
        batches = int(var.varValue or 0)
        if batches > 0:
            result[pname] = batches * batch_size
    return result


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    batch_size, budget, eff, preset_name, region_id, request_interval = _load_config()

    config = load_config()
    provider_csv     = REPO_ROOT / "Cache" / "Input" / "provider.csv"
    purchase_csv     = REPO_ROOT / "Cache" / "Input" / "purchase_list.csv"
    purchase_ore_csv = REPO_ROOT / "Cache" / "Output" / "purchase_list_ore.csv"
    reprocessing_json = REPO_ROOT / "Data" / "Reprocess" / "reprocessing_ores.json"
    preset_json      = REPO_ROOT / "Data" / "Materials" / "preset.json"
    alias_json       = REPO_ROOT / "Data" / "Materials" / "alias.json"

    cache_market_dir = resolve_path(config.get("paths", "market_cache_dir", fallback="Cache/Market"))
    region_name_map = {
        10000002: "jita",
        10000003: "vale_of_the_silent",
    }
    region_key = region_name_map.get(region_id, f"region_{region_id}")
    cache_market = cache_market_dir / f"price_{preset_name}.json"
    if not cache_market.exists():
        named_cache = cache_market_dir / f"{preset_name}_{region_name_map.get(region_id, f'region_{region_id}')}_{region_id}.json"
        if named_cache.exists():
            cache_market = named_cache

    temp_dir = resolve_path(config.get("paths", "temp_cache_dir", fallback="Cache/Temp"))

    provider_dict = load_provider(provider_csv)
    reprocessing_data, ore_to_materials, norm_eff = load_reprocessing_data(reprocessing_json, eff)
    provider_to_ore_id = build_provider_ore_mapping(provider_dict.keys(), reprocessing_data)

    preset_type_ids, preset_types_map, mineral_id_to_name = load_preset_type_ids(
        preset_json, alias_json, preset_name, REPO_ROOT
    )

    if not cache_market.exists():
        run_price_fetcher(REPO_ROOT, preset_name, region_id, request_interval)
    else:
        print(f"检测到已有市场缓存，跳过拉价: {cache_market}")

    mineral_id_to_price = load_prices(cache_market, region_key=region_key)

    # 加载采购目标（type_id → quantity）
    purchase_list: dict = {}
    unmatched_purchase_names = []
    local_name_to_id = name_to_id(preset_types_map, languages=("zh",))
    with purchase_csv.open("r", encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) < 2:
                continue
            name = row[0].strip()
            qty = int(row[1])
            if name in local_name_to_id:
                purchase_list[local_name_to_id[name]] = qty
            else:
                unmatched_purchase_names.append(name)

    print(f"[DEBUG] provider 数量: {len(provider_dict)}")
    print(f"[DEBUG] 精炼效率配置={eff}，实际使用={norm_eff:.6f}")
    print(f"[DEBUG] reprocessing 矿石条目: {len(reprocessing_data)}")
    print(f"[DEBUG] preset 物料数量: {len(preset_type_ids)}")
    print(f"[DEBUG] purchase 目标数量: {len(purchase_list)}")
    if unmatched_purchase_names:
        print(f"[DEBUG] purchase_list 中未映射名称({len(unmatched_purchase_names)}): {unmatched_purchase_names[:10]}")

    unresolved = [name for name, ore_id in provider_to_ore_id.items() if ore_id is None]
    if unresolved:
        print(f"[DEBUG] provider 中未匹配到矿石 ID({len(unresolved)}): {unresolved[:10]}")

    # 调试快照
    ore_yield_snapshot = [
        {"id": ore.get("id"), "zh": ore.get("zh"), "materials": ore_to_materials.get(ore.get("id"), [])}
        for ore in reprocessing_data
    ]
    dump_temp_json(temp_dir, "reprocessing_yield_snapshot.json", {
        "eff_config": eff, "eff_used": norm_eff,
        "ore_count": len(ore_yield_snapshot), "ores": ore_yield_snapshot,
    })
    dump_temp_json(temp_dir, "provider_parsed.json", provider_dict)
    dump_temp_json(temp_dir, "provider_ore_mapping.json", provider_to_ore_id)
    dump_temp_json(temp_dir, "purchase_mapping.json", {
        "purchase_list": purchase_list,
        "unmatched_purchase_names": unmatched_purchase_names,
    })

    # 联立 ILP 求解
    used_ores_total = solve_ore_selection(
        provider_dict=provider_dict,
        provider_to_ore_id=provider_to_ore_id,
        ore_to_materials=ore_to_materials,
        purchase_list=purchase_list,
        mineral_id_to_price=mineral_id_to_price,
        preset_type_ids=preset_type_ids,
        batch_size=batch_size,
        budget=budget,
    )

    # 计算实际满足情况
    actual_yield: dict = {mid: 0 for mid in purchase_list}
    for pname, qty in used_ores_total.items():
        ore_id = provider_to_ore_id.get(pname)
        if ore_id is None:
            continue
        batches = qty // batch_size
        for mat in ore_to_materials.get(ore_id, []):
            mid = mat["materialTypeID"]
            if mid in actual_yield:
                actual_yield[mid] += mat["quantity"] * batches

    # 写入结果
    purchase_ore_csv.parent.mkdir(parents=True, exist_ok=True)
    with purchase_ore_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        for pname, qty in used_ores_total.items():
            writer.writerow([pname, qty])

    dump_temp_json(temp_dir, "solver_result_summary.json", {
        "used_ores_total": used_ores_total,
        "actual_yield": actual_yield,
        "purchase_list": purchase_list,
    })

    # 打印统计
    total_ore_cost = 0.0
    print("\n===== 联立 ILP 矿石选择结果 =====")
    for pname, qty in used_ores_total.items():
        print(f"- {pname}：{qty} units")

    print("\n目标矿物满足情况：")
    for mid, demand in purchase_list.items():
        name = mineral_id_to_name.get(mid, str(mid))
        done = actual_yield.get(mid, 0)
        pct = (done / demand * 100) if demand > 0 else 0
        print(f"- {name}：{done} / {demand}（满足比例：{pct:.1f}%）")


if __name__ == "__main__":
    main()
