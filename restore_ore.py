import configparser
import csv
import json
import math
import subprocess
import sys
from pathlib import Path

from Utilities.name_mapping import id_to_name, load_types_map, name_to_id
from pulp import LpInteger, LpMaximize, LpProblem, LpStatus, LpVariable, PULP_CBC_CMD, lpSum


def find_repo_root() -> Path:
    current_dir = Path(__file__).resolve().parent
    return next((p for p in [current_dir, *current_dir.parents] if (p / "config.ini").exists()), current_dir)


def load_config(config_path: Path):
    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf-8")

    batch_size = config.getint("reprocessing_solver", "batch_size", fallback=100)
    budget = config.getfloat("reprocessing_solver", "budget", fallback=350_000_000)
    eff = config.getfloat("reprocessing_solver", "eff", fallback=0.9063)

    preset_name = config.get("reprocessing_market", "preset", fallback="mineral&element")
    region_id = config.getint("reprocessing_market", "region_id", fallback=10000002)
    request_interval = config.getfloat("reprocessing_market", "request_interval", fallback=0.05)

    return batch_size, budget, eff, preset_name, region_id, request_interval


def load_provider(provider_csv: Path):
    provider_dict = {}
    with provider_csv.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue

            if "	" in line:
                parts = [part.strip() for part in line.split("	") if part.strip()]
            else:
                parts = line.split()

            if len(parts) < 2:
                continue

            name = parts[0]
            qty_text = "".join(parts[1:]).replace(",", "").replace("，", "")
            if not qty_text.isdigit():
                continue

            provider_dict[name] = int(qty_text)
    return provider_dict


def normalize_reprocessing_eff(eff: float) -> float:
    if eff <= 0:
        raise ValueError(f"无效精炼效率 eff={eff}，必须大于0")
    if eff > 1:
        return eff / 100
    return eff


def load_reprocessing_data(path: Path, eff: float):
    with path.open("r", encoding="utf-8") as f:
        reprocessing_data = json.load(f)

    norm_eff = normalize_reprocessing_eff(eff)
    ore_to_materials = {ore["id"]: ore.get("materials", []) for ore in reprocessing_data}
    for ore_id, mats in ore_to_materials.items():
        for mat in mats:
            mat["quantity"] = max(0, math.floor(mat["quantity"] * norm_eff))

    return reprocessing_data, ore_to_materials, norm_eff


def load_prices(price_json: Path):
    with price_json.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {int(item["id"]): item.get("buy", 0) for item in data}


def load_preset_type_ids(preset_json: Path, alias_json: Path, preset_name: str, repo_root: Path):
    with preset_json.open("r", encoding="utf-8") as f:
        presets = json.load(f)
    with alias_json.open("r", encoding="utf-8") as f:
        alias_data = json.load(f)

    alias_map = {item["alias"]: item["path"] for item in alias_data.get("aliases", [])}
    preset = next((p for p in presets if p.get("name") == preset_name), None)
    if preset is None:
        raise ValueError(f"preset 不存在: {preset_name}")

    type_ids = set()
    preset_types_map: dict[int, dict] = {}
    for alias in preset.get("children", []):
        rel_path = alias_map.get(alias)
        if not rel_path:
            raise ValueError(f"alias 不存在: {alias}")
        child_types_map = load_types_map(repo_root / rel_path)
        type_ids.update(child_types_map.keys())
        preset_types_map.update(child_types_map)

    return type_ids, preset_types_map, id_to_name(preset_types_map)


def run_price_fetcher(repo_root: Path, preset_name: str, region_id: int, request_interval: float):
    script = repo_root / "Utilities" / "get_price_by_preset.py"
    cmd = [
        sys.executable,
        str(script),
        preset_name,
        "--region-id",
        str(region_id),
        "--request-interval",
        str(request_interval),
    ]
    subprocess.run(cmd, cwd=repo_root, check=True)


def main():
    repo_root = find_repo_root()
    config_path = repo_root / "config.ini"

    batch_size, budget, eff, preset_name, region_id, request_interval = load_config(config_path)

    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf-8")

    provider_csv = repo_root / "Cache" / "Input" / "provider.csv"
    purchase_csv = repo_root / "Cache" / "Input" / "purchase_list.csv"
    purchase_ore_csv = repo_root / "Cache" / "Output" / "purchase_list_ore.csv"

    reprocessing_json = repo_root / "Data" / "Reprocess" / "reprocessing_ores.json"
    preset_json = repo_root / "Data" / "Materials" / "preset.json"
    alias_json = repo_root / "Data" / "Materials" / "alias.json"
    cache_market_dir = Path(config.get("paths", "market_cache_dir", fallback="Cache/Market"))
    if not cache_market_dir.is_absolute():
        cache_market_dir = repo_root / cache_market_dir
    cache_market = cache_market_dir / f"{preset_name}_region_{region_id}.json"

    provider_dict = load_provider(provider_csv)
    reprocessing_data, ore_to_materials, norm_eff = load_reprocessing_data(reprocessing_json, eff)

    preset_type_ids, preset_types_map, mineral_id_to_name = load_preset_type_ids(
        preset_json, alias_json, preset_name, repo_root
    )

    if not cache_market.exists():
        run_price_fetcher(repo_root, preset_name, region_id, request_interval)
    else:
        print(f"检测到已有市场缓存，跳过拉价: {cache_market}")

    mineral_id_to_price = load_prices(cache_market)

    purchase_list = {}
    unmatched_purchase_names = []
    local_name_to_id = name_to_id(preset_types_map, languages=("zh",))
    with purchase_csv.open("r", encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="	"):
            if len(row) < 2:
                continue
            name = row[0].strip()
            qty = int(row[1])
            if name in local_name_to_id:
                purchase_list[local_name_to_id[name]] = qty
            else:
                unmatched_purchase_names.append(name)

    print(f"[DEBUG] provider数量: {len(provider_dict)}")
    print(f"[DEBUG] 精炼效率配置={eff}，实际使用={norm_eff:.6f}")
    print(f"[DEBUG] reprocessing矿石条目: {len(reprocessing_data)}")
    print(f"[DEBUG] preset物料数量: {len(preset_type_ids)}")
    print(f"[DEBUG] purchase目标数量: {len(purchase_list)}")
    if unmatched_purchase_names:
        print(f"[DEBUG] purchase_list中未映射名称({len(unmatched_purchase_names)}): {unmatched_purchase_names[:10]}")

    remaining_budget = budget
    remaining_demand = purchase_list.copy()
    used_ores_total = {}

    sorted_targets = sorted(
        purchase_list.items(), key=lambda x: mineral_id_to_price.get(x[0], 0), reverse=True
    )

    for mid, _ in sorted_targets:
        target_name = mineral_id_to_name.get(mid, str(mid))
        if remaining_demand[mid] <= 0:
            print(f"[DEBUG] 跳过目标 {target_name}({mid})，原因: 需求已满足")
            continue
        if remaining_budget <= 0:
            print(f"[DEBUG] 跳过目标 {target_name}({mid})，原因: 预算耗尽")
            continue

        relevant_ores = []
        for pname in provider_dict:
            ore_id = next((o["id"] for o in reprocessing_data if o["zh"] in pname), None)
            if ore_id is None:
                continue
            mats = ore_to_materials[ore_id]
            if any(mat["materialTypeID"] == mid for mat in mats):
                relevant_ores.append(pname)
        if not relevant_ores:
            print(f"[DEBUG] 目标 {target_name}({mid}) 无可用矿石（provider与reprocessing匹配后为空）")
            continue

        print(
            f"[DEBUG] 开始求解目标 {target_name}({mid})，剩余需求={remaining_demand[mid]}，候选矿石数={len(relevant_ores)}，剩余预算={remaining_budget:.2f}"
        )
        prob = LpProblem(f"Step_{mid}", LpMaximize)
        x_vars = {}
        for pname in relevant_ores:
            max_batches = provider_dict[pname] // batch_size
            x_vars[pname] = LpVariable(pname, 0, max_batches, cat=LpInteger)

        coeffs = []
        vars_list = []
        for pname, var in x_vars.items():
            ore_id = next((o["id"] for o in reprocessing_data if o["zh"] in pname), None)
            for mat in ore_to_materials[ore_id]:
                if mat["materialTypeID"] == mid:
                    coeffs.append(mat["quantity"])
                    vars_list.append(var)
        prob += lpSum([c * v for c, v in zip(coeffs, vars_list)]) <= remaining_demand[mid], f"Demand_{mid}"

        extra_terms = []
        for pname, var in x_vars.items():
            ore_id = next((o["id"] for o in reprocessing_data if o["zh"] in pname), None)
            for mat in ore_to_materials[ore_id]:
                if mat["materialTypeID"] != mid:
                    extra_terms.append(
                        mat["quantity"] * var * mineral_id_to_price.get(mat["materialTypeID"], 0)
                    )
        if extra_terms:
            prob += lpSum(extra_terms) <= remaining_budget, "BudgetConstraint"

        objective_terms = []
        for pname, var in x_vars.items():
            ore_id = next((o["id"] for o in reprocessing_data if o["zh"] in pname), None)
            for mat in ore_to_materials[ore_id]:
                if mat["materialTypeID"] == mid:
                    objective_terms.append(
                        mat["quantity"] * var * mineral_id_to_price.get(mat["materialTypeID"], 0)
                    )
        prob += lpSum(objective_terms), f"MaxTarget_{mid}"

        prob.solve(PULP_CBC_CMD(msg=0))

        selected_batches = {pname: int(var.varValue or 0) for pname, var in x_vars.items() if int(var.varValue or 0) > 0}
        print(
            f"[DEBUG] 求解完成目标 {target_name}({mid})，状态={LpStatus.get(prob.status, prob.status)}，选中矿石种类={len(selected_batches)}"
        )
        if selected_batches:
            print(f"[DEBUG] 选中批次数详情: {selected_batches}")

        for pname, var in x_vars.items():
            val = int(var.varValue or 0)
            if val <= 0:
                continue
            used_ores_total[pname] = used_ores_total.get(pname, 0) + val * batch_size
            ore_id = next((o["id"] for o in reprocessing_data if o["zh"] in pname), None)
            for mat in ore_to_materials[ore_id]:
                mid_mat = mat["materialTypeID"]
                produced_qty = mat["quantity"] * val
                if mid_mat in remaining_demand:
                    remaining_demand[mid_mat] = max(0, remaining_demand[mid_mat] - produced_qty)
                elif mid_mat in preset_type_ids:
                    remaining_budget = max(
                        0,
                        remaining_budget
                        - produced_qty * mineral_id_to_price.get(mid_mat, 0),
                    )

    purchase_ore_csv.parent.mkdir(parents=True, exist_ok=True)
    with purchase_ore_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        for pname, qty in used_ores_total.items():
            writer.writerow([pname, qty])

    print("\n===== 分步 ILP 统计表 =====")
    print(f"剩余预算：{remaining_budget:.2f}\n")

    for pname, qty in used_ores_total.items():
        print(f"- {pname}：{qty} units")

    print("\n目标矿物剩余需求：")
    for mid, demand_left in remaining_demand.items():
        name = mineral_id_to_name.get(mid, str(mid))
        total = purchase_list[mid]
        done = total - demand_left
        pct = (done / total * 100) if total > 0 else 0
        print(f"- {name}：{done} / {total}（已满足比例：{pct:.1f}%）")


if __name__ == "__main__":
    main()
