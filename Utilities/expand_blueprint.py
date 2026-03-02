import argparse
import configparser
import json
from collections import defaultdict
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
    seen = set()
    ordered = []
    for item in priority:
        if item and item not in seen:
            seen.add(item)
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


def _material_signature(materials_map):
    return tuple(sorted((int(type_id), float(quantity)) for type_id, quantity in materials_map.items()))


def expand_blueprint(blueprint_id, activity="manufacturing", blueprints_yaml_path=None, max_iterations=50):
    blueprint_id = int(blueprint_id)
    activity = activity.lower()

    blueprints = _load_blueprints(blueprints_yaml_path=blueprints_yaml_path)
    bp_data = blueprints.get(blueprint_id)
    if not bp_data:
        raise KeyError(f"未找到蓝图 {blueprint_id}")

    activity_data = bp_data.get("activities", {}).get(activity)
    if not activity_data:
        raise KeyError(f"蓝图 {blueprint_id} 不包含活动 {activity}")

    materials_map = defaultdict(float)
    for material in activity_data.get("materials", []):
        materials_map[int(material["typeID"])] += float(material.get("quantity", 0))

    product_index = _build_product_index(blueprints, preferred_activity=activity)

    iteration_count = 0
    seen_signatures = {_material_signature(materials_map)}

    while iteration_count < max_iterations:
        expanded_any = False
        next_materials = defaultdict(float)

        for material_type_id, material_qty in materials_map.items():
            producer = product_index.get(int(material_type_id))
            if producer is None:
                next_materials[int(material_type_id)] += material_qty
                continue

            source_blueprint = blueprints.get(producer["blueprint_id"], {})
            source_activity = producer["activity"]
            source_materials = source_blueprint.get("activities", {}).get(source_activity, {}).get("materials", [])

            if not source_materials:
                next_materials[int(material_type_id)] += material_qty
                continue

            expanded_any = True
            product_qty = float(producer.get("product_quantity", 1) or 1)
            ratio = material_qty / product_qty

            for source_material in source_materials:
                next_materials[int(source_material["typeID"])] += float(source_material.get("quantity", 0)) * ratio

        if not expanded_any:
            break

        iteration_count += 1
        signature = _material_signature(next_materials)
        if signature in seen_signatures:
            break

        seen_signatures.add(signature)
        materials_map = next_materials

    expanded_materials = [
        {"typeID": int(type_id), "quantity": quantity}
        for type_id, quantity in sorted(materials_map.items(), key=lambda item: item[0])
    ]

    return {
        "blueprint_id": blueprint_id,
        "activity": activity,
        "iterations": iteration_count,
        "materials": expanded_materials,
    }


def main():
    parser = argparse.ArgumentParser(description="展开蓝图材料直到材料无法继续由其它蓝图产出")
    parser.add_argument("blueprint_id", type=int, help="蓝图ID")
    parser.add_argument("--activity", default="manufacturing", help="活动类型，默认 manufacturing")
    parser.add_argument("--blueprints-path", help="可选蓝图文件路径（yaml/json）")
    parser.add_argument("--max-iterations", type=int, default=50, help="最大迭代次数")
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
