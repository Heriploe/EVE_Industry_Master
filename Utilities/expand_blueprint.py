import argparse
import configparser
import json
from pathlib import Path

REPO_ROOT = next(
    (p for p in [Path(__file__).resolve().parent, *Path(__file__).resolve().parent.parents] if (p / "config.ini").exists()),
    Path(__file__).resolve().parent,
)


def _resolve_shared_path(config_key, default_rel_path):
    config = configparser.ConfigParser()
    config.read(REPO_ROOT / "config.ini", encoding="utf-8")

    path_value = config.get("paths", config_key, fallback=default_rel_path)
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate


def _load_blueprints(blueprints_yaml_path=None):
    path = Path(blueprints_yaml_path) if blueprints_yaml_path else _resolve_shared_path("blueprints_yaml", "Data/blueprints.yaml")

    if path.suffix.lower() == ".json":
        with open(path, "r", encoding="utf-8") as f:
            blueprint_list = json.load(f)
        return {
            int(item["blueprintTypeID"]): {
                "activities": {k: v for k, v in item.items() if k in {"manufacturing", "reaction", "copying", "invention"}}
            }
            for item in blueprint_list
            if "blueprintTypeID" in item
        }

    try:
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ModuleNotFoundError:
        fallback_path = REPO_ROOT / "Optimized Calculator/Source/blueprints_merged.json"
        with open(fallback_path, "r", encoding="utf-8") as f:
            blueprint_list = json.load(f)
        return {
            int(item["blueprintTypeID"]): {
                "activities": {k: v for k, v in item.items() if k in {"manufacturing", "reaction", "copying", "invention"}}
            }
            for item in blueprint_list
            if "blueprintTypeID" in item
        }


def _build_product_index(blueprints, preferred_activity):
    priority = [preferred_activity, "manufacturing", "reaction", "copying", "invention"]
    ordered = []
    for item in priority:
        if item and item not in ordered:
            ordered.append(item)

    index = {}
    for activity in ordered:
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
    products = []
    for product in activity_data.get("products", []):
        products.append(
            {
                "typeID": int(product.get("typeID", -1)),
                "quantity": float(product.get("quantity", 0) or 0),
            }
        )
    return products


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
        "children": [],
    }

    expanded_steps = 0

    for material in activity_data.get("materials", []):
        material_type_id = int(material["typeID"])
        material_qty = float(material.get("quantity", 0)) * required_runs

        material_entry = {
            "typeID": material_type_id,
            "quantity": material_qty,
        }

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

        node["materials"].append(material_entry)
        node["children"].append(child_node)

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

    return {
        "blueprint": tree,
        "iterations": executed_steps,
    }


def main():
    parser = argparse.ArgumentParser(description="展开蓝图为嵌套结构，children 为可制造的下游蓝图")
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
