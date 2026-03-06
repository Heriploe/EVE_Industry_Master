import argparse
import json
import re
from pathlib import Path

from Utilities.name_mapping import load_types_map

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CORP_DIR = REPO_ROOT / "Cache" / "Asset" / "Corp"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "Cache" / "Output" / "Blueprints"
DEFAULT_TYPES_FILE = REPO_ROOT / "Data" / "types.json"


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]", "_", value or "")
    cleaned = cleaned.strip().strip(".")
    return cleaned or "unnamed"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_name_index(names_raw):
    by_item_id = {}
    by_name = {}
    for row in names_raw:
        item_id = row.get("item_id")
        name = row.get("name")
        if item_id is None or not name:
            continue
        by_item_id[item_id] = name
        by_name.setdefault(name, []).append(item_id)
    return by_item_id, by_name


def enrich_blueprint(bp, type_map, container_name):
    type_id = bp.get("type_id")
    names = type_map.get(type_id, {"zh": "", "en": ""})
    return {
        "item_id": bp.get("item_id"),
        "id": type_id,
        "zh": names.get("zh", ""),
        "en": names.get("en", ""),
        "material_efficiency": bp.get("material_efficiency", 0),
        "time_efficiency": bp.get("time_efficiency", 0),
        "runs": bp.get("runs", -1),
        "quantity": bp.get("quantity", -1),
        "location_id": bp.get("location_id"),
        "location_flag": bp.get("location_flag"),
        "container_name": container_name,
    }


def export_for_container(container_name, container_id, blueprints, type_map, output_dir: Path):
    matched = [bp for bp in blueprints if bp.get("location_id") == container_id]
    enriched = [enrich_blueprint(bp, type_map, container_name) for bp in matched]

    filename = f"{sanitize_filename(container_name)}_{container_id}.json"
    output_path = output_dir / filename
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)
    return output_path, len(enriched)


def main():
    parser = argparse.ArgumentParser(description="按容器名称导出对应蓝图到 Cache/Output/Blueprints")
    parser.add_argument("--name", help="容器名称（精确匹配）。不传则导出所有容器。")
    parser.add_argument("--corp-dir", type=Path, default=DEFAULT_CORP_DIR, help="Corp 资产目录")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="导出目录")
    parser.add_argument("--types-file", type=Path, default=DEFAULT_TYPES_FILE, help="types.json 路径")
    args = parser.parse_args()

    blueprints_raw_path = args.corp_dir / "blueprints_raw.json"
    names_raw_path = args.corp_dir / "names_raw.json"

    if not blueprints_raw_path.exists():
        raise FileNotFoundError(f"未找到文件: {blueprints_raw_path}")
    if not names_raw_path.exists():
        raise FileNotFoundError(f"未找到文件: {names_raw_path}。请先运行 get_asset.py 生成 names_raw.json")

    blueprints_raw = load_json(blueprints_raw_path)
    names_raw = load_json(names_raw_path)
    type_map = load_types_map(str(args.types_file))

    _, by_name = build_name_index(names_raw)

    if args.name:
        target_items = by_name.get(args.name, [])
        if not target_items:
            print(f"未找到容器名: {args.name}")
            return
        targets = [(args.name, item_id) for item_id in target_items]
    else:
        targets = []
        for name, item_ids in by_name.items():
            for item_id in item_ids:
                targets.append((name, item_id))

    args.output_dir.mkdir(parents=True, exist_ok=True)

    total_files = 0
    total_blueprints = 0
    for name, item_id in targets:
        output_path, count = export_for_container(name, item_id, blueprints_raw, type_map, args.output_dir)
        total_files += 1
        total_blueprints += count
        print(f"导出: {output_path} (blueprints={count})")

    print(f"完成: files={total_files}, blueprints={total_blueprints}, output={args.output_dir}")


if __name__ == "__main__":
    main()
