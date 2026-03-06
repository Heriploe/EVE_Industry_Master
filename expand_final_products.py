import argparse
import configparser
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

from Utilities.industry_cost import invention_T2_runs
from Utilities.name_mapping import load_types_map, name_to_id

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_FINAL_PRODUCTS = "Cache/Output/final_products.csv"
DEFAULT_INVENTORY_JSON = "Cache/Asset/Corp/final_non_blueprints.json"
DEFAULT_OUTPUT_DIR = "Cache/Output/Expand_Final_Products"

ACTIVITY_PRIORITY = ["manufacturing", "reaction", "copying", "invention"]


def resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p


def load_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(REPO_ROOT / "config.ini", encoding="utf-8")
    return config


def parse_name_quantity(line: str) -> Tuple[str, float]:
    raw = line.strip()
    if not raw:
        return "", 0.0

    if "\t" in raw:
        name, qty = raw.rsplit("\t", 1)
    elif "," in raw:
        name, qty = raw.rsplit(",", 1)
    elif " " in raw:
        name, qty = raw.rsplit(" ", 1)
    else:
        return raw, 0.0

    try:
        q = float(qty.strip())
    except ValueError:
        q = 0.0
    return name.strip(), q


def load_simple_lines(path: Path) -> List[str]:
    with path.open("r", encoding="utf-8-sig") as f:
        return [line.rstrip("\n") for line in f if line.strip()]


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
                raise RuntimeError("未安装 PyYAML，且未找到 JSON 回退文件 Cache/Input/blueprints_merged.json")
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


def format_quantity(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def write_space_csv(path: Path, rows: List[Tuple[str, float]]):
    with path.open("w", encoding="utf-8-sig") as f:
        for name, qty in rows:
            f.write(f"{name} {format_quantity(float(qty))}\n")


def consume_inventory(type_id: int, quantity: float, inventory: Dict[int, float]) -> float:
    available = float(inventory.get(type_id, 0) or 0)
    used = min(available, quantity)
    if used > 0:
        inventory[type_id] = available - used
    return quantity - used


def main():
    config = load_config()
    output_dir_from_config = config.get("calculator", "output_dir", fallback="Cache/Output")
    default_final = str((resolve_path(output_dir_from_config) / "final_products.csv").relative_to(REPO_ROOT))

    parser = argparse.ArgumentParser(description="按层展开 final_products，并输出子蓝图执行次数与缺料")
    parser.add_argument("--final-products", default=default_final or DEFAULT_FINAL_PRODUCTS, help="输入 final_products.csv")
    parser.add_argument("--inventory-json", default=DEFAULT_INVENTORY_JSON, help="库存文件 final_non_blueprints.json")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录")
    parser.add_argument("--max-depth", type=int, default=0, help="展开深度，默认 0")
    args = parser.parse_args()

    if args.max_depth < 0:
        raise ValueError("max-depth 不能小于 0")

    final_products_path = resolve_path(args.final_products)
    inventory_path = resolve_path(args.inventory_json)
    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not final_products_path.exists():
        raise FileNotFoundError(f"未找到 final_products.csv: {final_products_path}")
    if not inventory_path.exists():
        raise FileNotFoundError(f"未找到库存文件: {inventory_path}")

    types_json = resolve_path(config.get("paths", "types_json", fallback="Data/types.json"))
    blueprints_yaml = resolve_path(config.get("paths", "blueprints_yaml", fallback="Data/blueprints.yaml"))
    t2_t1_path = resolve_path(config.get("paths", "t2_t1_json", fallback="Data/T2_T1.json"))

    type_map = load_types_map(str(types_json))
    name2id = name_to_id(type_map)

    blueprints = load_blueprints(blueprints_yaml)
    product_index = build_product_index(blueprints)

    t2_t1_raw = load_json(t2_t1_path)
    t2_to_t1 = {int(pair[0]): int(pair[1]) for pair in t2_t1_raw if isinstance(pair, list) and len(pair) >= 2}

    inventory_raw = load_json(inventory_path)
    inventory = defaultdict(float)
    for row in inventory_raw:
        tid = row.get("id")
        qty = row.get("quantity", 0)
        if tid is None:
            continue
        inventory[int(tid)] += float(qty or 0)

    missing = Counter()
    child_execution = Counter()  # (bp_id, activity) -> runs
    invention_execution = Counter()  # t1 blueprint id -> invention runs

    def add_missing_material(type_id: int, qty: float):
        remaining = consume_inventory(type_id, qty, inventory)
        if remaining > 0:
            missing[int(type_id)] += remaining

    def expand_product(type_id: int, qty: float, depth: int, stack: Set[Tuple[int, str]]):
        remaining_need = consume_inventory(type_id, qty, inventory)
        if remaining_need <= 0:
            return

        producer = product_index.get(int(type_id))
        if producer is None:
            missing[int(type_id)] += remaining_need
            return

        bp_id = int(producer["blueprint_id"])
        activity = producer["activity"]
        product_qty = float(producer.get("product_quantity", 1) or 1)
        if product_qty <= 0:
            missing[int(type_id)] += remaining_need
            return

        runs = remaining_need / product_qty

        if depth > 0:
            child_execution[(bp_id, activity)] += runs

        if depth > 0 and bp_id in t2_to_t1:
            t1_bp_id = t2_to_t1[bp_id]
            invention_runs_per_unit, _, _ = invention_T2_runs()
            required_invention_runs = runs * float(invention_runs_per_unit)
            invention_execution[t1_bp_id] += required_invention_runs

            t1_invention = blueprints.get(t1_bp_id, {}).get("activities", {}).get("invention", {})
            for mat in t1_invention.get("materials", []):
                mat_id = int(mat.get("typeID"))
                mat_qty = float(mat.get("quantity", 0) or 0) * required_invention_runs
                add_missing_material(mat_id, mat_qty)

        act_data = blueprints.get(bp_id, {}).get("activities", {}).get(activity, {})
        if not act_data:
            return

        for mat in act_data.get("materials", []):
            mat_id = int(mat.get("typeID"))
            mat_qty = float(mat.get("quantity", 0) or 0) * runs

            if depth >= args.max_depth:
                add_missing_material(mat_id, mat_qty)
                continue

            cycle_key = (bp_id, activity)
            if cycle_key in stack:
                add_missing_material(mat_id, mat_qty)
                continue

            expand_product(mat_id, mat_qty, depth + 1, stack | {cycle_key})

    unresolved_names = []
    for line in load_simple_lines(final_products_path):
        name, qty = parse_name_quantity(line)
        if not name or qty <= 0:
            continue
        type_id = name2id.get(name)
        if type_id is None:
            unresolved_names.append(name)
            continue
        expand_product(int(type_id), float(qty), depth=0, stack=set())

    missing_rows = []
    for tid, qty in sorted(missing.items(), key=lambda x: x[0]):
        zh = (type_map.get(int(tid), {}) or {}).get("zh") or str(tid)
        missing_rows.append((zh, qty))

    execution_rows = []
    for (bp_id, _activity), runs in sorted(child_execution.items(), key=lambda x: (x[0][0], x[0][1])):
        zh = (type_map.get(int(bp_id), {}) or {}).get("zh") or str(bp_id)
        execution_rows.append((zh, runs))

    for t1_bp_id, inv_runs in sorted(invention_execution.items(), key=lambda x: x[0]):
        zh = (type_map.get(int(t1_bp_id), {}) or {}).get("zh") or str(t1_bp_id)
        execution_rows.append((f"{zh}（发明）", inv_runs))

    missing_csv = output_dir / "missing_materials.csv"
    execution_csv = output_dir / "expanded_blueprint_runs.csv"
    summary_json = output_dir / "summary.json"

    write_space_csv(missing_csv, sorted(missing_rows, key=lambda x: x[0]))
    write_space_csv(execution_csv, sorted(execution_rows, key=lambda x: x[0]))

    summary = {
        "final_products": str(final_products_path),
        "inventory_json": str(inventory_path),
        "max_depth": int(args.max_depth),
        "missing_material_types": len(missing_rows),
        "child_blueprints": len(child_execution),
        "invention_entries": len(invention_execution),
        "unresolved_product_names": unresolved_names,
        "outputs": {
            "missing_materials_csv": str(missing_csv),
            "expanded_blueprint_runs_csv": str(execution_csv),
        },
    }
    with summary_json.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
