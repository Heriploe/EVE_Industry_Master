"""
expand_blueprint.py
===================
蓝图依赖树展开工具。

修复：
  - 移除硬编码的旧 fallback 路径 "Optimized Calculator/Source/blueprints_merged.json"
  - 统一使用 blueprint_utils.load_blueprints_from_file 加载蓝图
  - 使用 config_utils.REPO_ROOT

兼容 Python 3.8+。
"""

import argparse
import json
from pathlib import Path

import sys as _us; _us.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from utilities.data.app_config import load_app_config as _load_cfg
from utilities.data.config_utils import load_config, resolve_config_path
try:
    _u_cfg, REPO_ROOT = _load_cfg()
except Exception:
    REPO_ROOT = Path(__file__).resolve().parent.parent
from utilities.blueprint.blueprint_utils import load_blueprints_from_file

ACTIVITY_PRIORITY = ["manufacturing", "reaction", "copying", "invention"]


def _load_blueprints(blueprints_yaml_path=None):
    if blueprints_yaml_path:
        path = Path(blueprints_yaml_path)
    else:
        cfg = load_config()
        path = resolve_config_path(cfg, "paths", "blueprints_yaml", "Data/blueprints.yaml")
    return load_blueprints_from_file(path)


def _build_product_index(blueprints, preferred_activity):
    priority = []
    for item in [preferred_activity] + ACTIVITY_PRIORITY:
        if item and item not in priority:
            priority.append(item)

    index = {}
    for activity in priority:
        for blueprint_id, bp_data in blueprints.items():
            activity_data = bp_data.get("activities", {}).get(activity)
            if not activity_data:
                continue
            for product in activity_data.get("products", []):
                product_id = int(product.get("typeID", -1))
                if product_id < 0 or product_id in index:
                    continue
                index[product_id] = {
                    "blueprint_id": int(blueprint_id),
                    "activity": activity,
                    "product_quantity": float(product.get("quantity", 1) or 1),
                }
    return index


def _get_products(blueprints, blueprint_id, activity):
    activity_data = blueprints.get(blueprint_id, {}).get("activities", {}).get(activity, {})
    return [
        {"typeID": int(p.get("typeID", -1)), "quantity": float(p.get("quantity", 0) or 0)}
        for p in activity_data.get("products", [])
    ]


def _expand_node(blueprint_id, activity, required_runs, blueprints, product_index, path, depth_left):
    activity_data = blueprints.get(blueprint_id, {}).get("activities", {}).get(activity)
    if not activity_data:
        raise KeyError(f"蓝图 {blueprint_id} 不包含活动 {activity}")

    node = {
        "blueprint_id": int(blueprint_id),
        "activity": activity,
        "runs": float(required_runs),
        "products": _get_products(blueprints, blueprint_id, activity),
        "materials": [],
    }

    expanded_steps = 0
    for material in activity_data.get("materials", []):
        material_type_id = int(material["typeID"])
        material_qty = float(material.get("quantity", 0)) * required_runs

        material_entry = {"typeID": material_type_id, "quantity": material_qty}

        producer = product_index.get(material_type_id)
        if producer is None or depth_left <= 0:
            node["materials"].append(material_entry)
            continue

        producer_blueprint = int(producer["blueprint_id"])
        producer_activity = producer["activity"]
        cycle_key = (producer_blueprint, producer_activity)
        if cycle_key in path:
            node["materials"].append(material_entry)
            continue

        product_qty = float(producer.get("product_quantity", 1) or 1)
        child_runs = material_qty / product_qty

        child_node, child_steps = _expand_node(
            blueprint_id=producer_blueprint,
            activity=producer_activity,
            required_runs=child_runs,
            blueprints=blueprints,
            product_index=product_index,
            path=path | {cycle_key},
            depth_left=depth_left - 1,
        )
        expanded_steps += 1 + child_steps

        material_entry["expanded_by"] = {
            "blueprint_id": producer_blueprint,
            "activity": producer_activity,
            "runs": child_runs,
        }
        material_entry["child_blueprint"] = child_node
        node["materials"].append(material_entry)

    return node, expanded_steps


def expand_blueprint(blueprint_id, activity="manufacturing", blueprints_yaml_path=None, max_iterations=50):
    blueprint_id = int(blueprint_id)
    activity = activity.lower()

    if max_iterations < 0:
        raise ValueError("max_iterations 不能小于 0")

    blueprints = _load_blueprints(blueprints_yaml_path=blueprints_yaml_path)
    if blueprint_id not in blueprints:
        raise KeyError(f"未找到蓝图 {blueprint_id}")

    product_index = _build_product_index(blueprints, preferred_activity=activity)

    tree, executed_steps = _expand_node(
        blueprint_id=blueprint_id,
        activity=activity,
        required_runs=1.0,
        blueprints=blueprints,
        product_index=product_index,
        path={(blueprint_id, activity)},
        depth_left=max_iterations,
    )

    return {"blueprint": tree, "iterations": executed_steps}


def main():
    parser = argparse.ArgumentParser(description="展开蓝图为嵌套结构，可制造材料直接嵌入 materials")
    parser.add_argument("blueprint_id", type=int, help="蓝图ID")
    parser.add_argument("--activity", default="manufacturing", help="活动类型，默认 manufacturing")
    parser.add_argument("--blueprints-path", help="可选蓝图文件路径（yaml/json）")
    parser.add_argument("--max-iterations", type=int, default=50, help="最大展开层数")
    parser.add_argument("--output-json", help="可选输出 JSON 路径")
    args = parser.parse_args()

    result = expand_blueprint(
        blueprint_id=args.blueprint_id,
        activity=args.activity,
        blueprints_yaml_path=args.blueprints_path,
        max_iterations=args.max_iterations,
    )

    if args.output_json:
        output_path = Path(args.output_json)
        if not output_path.is_absolute():
            output_path = REPO_ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"已保存结果到: {output_path}")

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
