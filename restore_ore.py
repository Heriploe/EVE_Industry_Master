import configparser
import csv
import json
import subprocess
import sys
from pathlib import Path

from pulp import LpInteger, LpMaximize, LpProblem, LpVariable, PULP_CBC_CMD, lpSum


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
        for row in csv.reader(f, delimiter="\t"):
            if len(row) < 2:
                continue
            provider_dict[row[0].strip()] = int(row[1])
    return provider_dict


def load_reprocessing_data(path: Path, eff: float):
    with path.open("r", encoding="utf-8") as f:
        reprocessing_data = json.load(f)

    ore_to_materials = {ore["id"]: ore.get("materials", []) for ore in reprocessing_data}
    for ore_id, mats in ore_to_materials.items():
        for mat in mats:
            mat["quantity"] = round(mat["quantity"] * eff)

    return reprocessing_data, ore_to_materials


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
    id_to_name = {}
    for alias in preset.get("children", []):
        rel_path = alias_map.get(alias)
        if not rel_path:
            raise ValueError(f"alias 不存在: {alias}")
        with (repo_root / rel_path).open("r", encoding="utf-8") as f:
            items = json.load(f)
        for item in items:
            tid = int(item["id"])
            type_ids.add(tid)
            id_to_name[tid] = item.get("zh", str(tid))

    return type_ids, id_to_name


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

    provider_csv = repo_root / "Cache" / "Input" / "provider.csv"
    purchase_csv = repo_root / "Cache" / "Input" / "purchase_list.csv"
    purchase_ore_csv = repo_root / "Cache" / "Input" / "purchase_list_ore.csv"

    reprocessing_json = repo_root / "Data" / "Reprocess" / "reprocessing_ores.json"
    preset_json = repo_root / "Data" / "Materials" / "preset.json"
    alias_json = repo_root / "Data" / "Materials" / "alias.json"
    cache_market = repo_root / "Cache" / "Market" / f"{preset_name}_region_{region_id}.json"

    provider_dict = load_provider(provider_csv)
    reprocessing_data, ore_to_materials = load_reprocessing_data(reprocessing_json, eff)

    preset_type_ids, mineral_id_to_name = load_preset_type_ids(preset_json, alias_json, preset_name, repo_root)

    run_price_fetcher(repo_root, preset_name, region_id, request_interval)
    mineral_id_to_price = load_prices(cache_market)

    purchase_list = {}
    name_to_id = {v: k for k, v in mineral_id_to_name.items()}
    with purchase_csv.open("r", encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) < 2:
                continue
            name = row[0].strip()
            qty = int(row[1])
            if name in name_to_id:
                purchase_list[name_to_id[name]] = qty

    remaining_budget = budget
    remaining_demand = purchase_list.copy()
    used_ores_total = {}

    sorted_targets = sorted(
        purchase_list.items(), key=lambda x: mineral_id_to_price.get(x[0], 0), reverse=True
    )

    for mid, _ in sorted_targets:
        if remaining_demand[mid] <= 0 or remaining_budget <= 0:
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
            continue

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
