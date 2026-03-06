import argparse
import configparser
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from Utilities.name_mapping import load_types_map

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT_BLUEPRINTS = "Cache/Output/Blueprints/A-优先_1051326279872.json"
DEFAULT_INVENTORY_JSON = "Cache/Asset/Corp/final_non_blueprints.json"
DEFAULT_OUTPUT_DIR = "Cache/Output/Expand_Blueprints"

ACTIVITY_PRIORITY = ["manufacturing", "reaction", "copying", "invention"]


def resolve_path(repo_root: Path, value: str) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = repo_root / p
    return p


def load_config(repo_root: Path) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(repo_root / "config.ini", encoding="utf-8")
    return config


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_blueprints(path: Path) -> dict:
    if path.suffix.lower() == ".json":
        items = load_json(path)
    else:
        try:
            import yaml

            with path.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except ModuleNotFoundError:
            fallback = REPO_ROOT / "Cache" / "Input" / "blueprints_merged.json"
            if not fallback.exists():
                raise RuntimeError(
                    "未安装 PyYAML，且未找到可用的 JSON 回退文件 Cache/Input/blueprints_merged.json"
                )
            items = load_json(fallback)

    result = {}
    for item in items:
        bp_id = item.get("blueprintTypeID")
        if bp_id is None:
            continue
        result[int(bp_id)] = {
            "activities": {
                key: value
                for key, value in item.items()
                if key in {"manufacturing", "reaction", "copying", "invention"}
            }
        }
    return result


def pick_activity(bp_data: dict) -> str:
    activities = bp_data.get("activities", {})
    for act in ACTIVITY_PRIORITY:
        if activities.get(act):
            return act
    return ""


def build_product_index(blueprints: dict) -> Dict[int, dict]:
    index = {}
    for activity in ACTIVITY_PRIORITY:
        for blueprint_id, bp_data in blueprints.items():
            act = bp_data.get("activities", {}).get(activity)
            if not act:
                continue
            for product in act.get("products", []):
                tid = int(product.get("typeID", -1))
                if tid < 0 or tid in index:
                    continue
                index[tid] = {
                    "blueprint_id": int(blueprint_id),
                    "activity": activity,
                    "product_quantity": float(product.get("quantity", 1) or 1),
                }
    return index


def build_type_name(type_map: dict, type_id: int) -> Tuple[str, str]:
    row = type_map.get(int(type_id), {}) if type_map else {}
    return row.get("zh", ""), row.get("en", "")


def expand_requirements(
    blueprint_id: int,
    activity: str,
    runs: float,
    depth: int,
    max_depth: int,
    blueprints: dict,
    product_index: dict,
    inventory: Dict[int, float],
    missing: Counter,
    execution_all: Counter,
    execution_child: Counter,
    trace: List[dict],
    stack: Tuple[Tuple[int, str], ...],
):
    execution_all[(int(blueprint_id), activity)] += runs
    if depth > 0:
        execution_child[(int(blueprint_id), activity)] += runs

    act_data = blueprints.get(int(blueprint_id), {}).get("activities", {}).get(activity)
    if not act_data:
        return

    for material in act_data.get("materials", []):
        mat_tid = int(material.get("typeID"))
        need_qty = float(material.get("quantity", 0) or 0) * runs

        available = float(inventory.get(mat_tid, 0) or 0)
        used = min(available, need_qty)
        if used > 0:
            inventory[mat_tid] = available - used
        remaining = need_qty - used

        trace.append(
            {
                "depth": depth,
                "blueprint_id": int(blueprint_id),
                "activity": activity,
                "material_type_id": mat_tid,
                "required": need_qty,
                "used_from_inventory": used,
                "remaining_after_inventory": remaining,
            }
        )

        if remaining <= 0:
            continue

        if depth < max_depth:
            producer = product_index.get(mat_tid)
            if producer:
                child_key = (int(producer["blueprint_id"]), producer["activity"])
                if child_key not in stack:
                    product_qty = float(producer.get("product_quantity", 1) or 1)
                    child_runs = remaining / product_qty if product_qty > 0 else 0
                    if child_runs > 0:
                        expand_requirements(
                            blueprint_id=child_key[0],
                            activity=child_key[1],
                            runs=child_runs,
                            depth=depth + 1,
                            max_depth=max_depth,
                            blueprints=blueprints,
                            product_index=product_index,
                            inventory=inventory,
                            missing=missing,
                            execution_all=execution_all,
                            execution_child=execution_child,
                            trace=trace,
                            stack=stack + (child_key,),
                        )
                        continue

        missing[mat_tid] += remaining


def write_missing_csv(path: Path, missing: Counter, type_map: dict):
    rows = []
    for tid, qty in sorted(missing.items(), key=lambda x: x[0]):
        zh, en = build_type_name(type_map, tid)
        rows.append(
            {
                "type_id": tid,
                "zh": zh,
                "en": en,
                "missing_quantity": round(float(qty), 6),
            }
        )

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["type_id", "zh", "en", "missing_quantity"])
        writer.writeheader()
        writer.writerows(rows)


def write_execution_csv(path: Path, execution: Counter, type_map: dict):
    rows = []
    for (bp_id, activity), runs in sorted(execution.items(), key=lambda x: (x[0][0], x[0][1])):
        zh, en = build_type_name(type_map, bp_id)
        rows.append(
            {
                "blueprint_id": bp_id,
                "zh": zh,
                "en": en,
                "activity": activity,
                "runs": round(float(runs), 6),
            }
        )

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["blueprint_id", "zh", "en", "activity", "runs"])
        writer.writeheader()
        writer.writerows(rows)


def main():
    config = load_config(REPO_ROOT)

    default_input = config.get("expand_blueprints_by_container", "input_blueprints_json", fallback=DEFAULT_INPUT_BLUEPRINTS)
    default_inventory = config.get("expand_blueprints_by_container", "inventory_json", fallback=DEFAULT_INVENTORY_JSON)
    default_output = config.get("expand_blueprints_by_container", "output_dir", fallback=DEFAULT_OUTPUT_DIR)
    default_depth = config.getint("expand_blueprints_by_container", "max_depth", fallback=0)
    default_root_runs = config.getfloat("expand_blueprints_by_container", "root_runs", fallback=1.0)

    parser = argparse.ArgumentParser(description="按容器蓝图文件分层展开物料，并结合库存扣减后导出缺料与子蓝图执行次数")
    parser.add_argument("--input-blueprints", default=default_input, help="输入蓝图 JSON（get_bluepirnts_by_container 输出格式）")
    parser.add_argument("--inventory-json", default=default_inventory, help="库存 JSON（final_non_blueprints.json）")
    parser.add_argument("--output-dir", default=default_output, help="输出目录")
    parser.add_argument("--max-depth", type=int, default=default_depth, help="展开层数，默认 0")
    parser.add_argument("--root-runs", type=float, default=default_root_runs, help="每个根蓝图执行次数，默认 1")
    args = parser.parse_args()

    if args.max_depth < 0:
        raise ValueError("max-depth 不能小于 0")
    if args.root_runs <= 0:
        raise ValueError("root-runs 必须大于 0")

    input_blueprints_path = resolve_path(REPO_ROOT, args.input_blueprints)
    inventory_path = resolve_path(REPO_ROOT, args.inventory_json)
    output_dir = resolve_path(REPO_ROOT, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_blueprints_path.exists():
        raise FileNotFoundError(f"未找到输入蓝图文件: {input_blueprints_path}")
    if not inventory_path.exists():
        raise FileNotFoundError(f"未找到库存文件: {inventory_path}")

    types_json = resolve_path(REPO_ROOT, config.get("paths", "types_json", fallback="Data/types.json"))
    blueprints_yaml = resolve_path(REPO_ROOT, config.get("paths", "blueprints_yaml", fallback="Data/blueprints.yaml"))

    container_blueprints = load_json(input_blueprints_path)
    inventory_raw = load_json(inventory_path)

    type_map = load_types_map(str(types_json))
    blueprints = load_blueprints(blueprints_yaml)
    product_index = build_product_index(blueprints)

    inventory = defaultdict(float)
    for row in inventory_raw:
        tid = row.get("id")
        qty = row.get("quantity", 0)
        if tid is None:
            continue
        inventory[int(tid)] += float(qty or 0)

    missing = Counter()
    execution_all = Counter()
    execution_child = Counter()
    trace = []

    for row in container_blueprints:
        bp_id = int(row.get("id"))
        bp_data = blueprints.get(bp_id)
        if not bp_data:
            continue

        activity = pick_activity(bp_data)
        if not activity:
            continue

        row_runs = row.get("runs", 1)
        try:
            row_runs = float(row_runs)
        except (TypeError, ValueError):
            row_runs = 1.0
        if row_runs <= 0:
            row_runs = 1.0

        effective_runs = float(args.root_runs) * row_runs

        expand_requirements(
            blueprint_id=bp_id,
            activity=activity,
            runs=effective_runs,
            depth=0,
            max_depth=args.max_depth,
            blueprints=blueprints,
            product_index=product_index,
            inventory=inventory,
            missing=missing,
            execution_all=execution_all,
            execution_child=execution_child,
            trace=trace,
            stack=((bp_id, activity),),
        )

    missing_csv = output_dir / "missing_materials.csv"
    execution_csv = output_dir / "expanded_blueprint_runs.csv"
    trace_json = output_dir / "expand_trace.json"
    summary_json = output_dir / "summary.json"

    write_missing_csv(missing_csv, missing, type_map)
    write_execution_csv(execution_csv, execution_child if args.max_depth > 0 else Counter(), type_map)

    with trace_json.open("w", encoding="utf-8") as f:
        json.dump(trace, f, ensure_ascii=False, indent=2)

    summary = {
        "input_blueprints": str(input_blueprints_path),
        "inventory_json": str(inventory_path),
        "max_depth": int(args.max_depth),
        "root_runs": float(args.root_runs),
        "root_blueprints": len(container_blueprints),
        "child_blueprints_executed": len(execution_child),
        "missing_material_types": len(missing),
        "outputs": {
            "missing_materials_csv": str(missing_csv),
            "expanded_blueprint_runs_csv": str(execution_csv),
            "expand_trace_json": str(trace_json),
        },
    }
    with summary_json.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
