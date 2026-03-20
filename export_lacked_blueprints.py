import argparse
import json
import re
from pathlib import Path

from Utilities.name_mapping import load_types_map


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_ALL_BLUEPRINTS_YAML = REPO_ROOT / "Cache/Input/blueprints.yaml"
DEFAULT_OWNED_BLUEPRINT_MAP = REPO_ROOT / "Cache/Asset/Corp/blueprint_id_name_map.json"
DEFAULT_T2_BLUEPRINTS = REPO_ROOT / "Cache/Input/T2.json"
DEFAULT_OUTPUT = REPO_ROOT / "Cache/Asset/Corp/Lacked_blueprints.json"
DEFAULT_NAMES_CSV_OUTPUT = REPO_ROOT / "Cache/Asset/Corp/Lacked_blueprints_names.csv"
DEFAULT_BROUGHT_BLUEPRINTS_CSV = REPO_ROOT / "Cache/Asset/Corp/brought_blueprints.csv"
DEFAULT_EXCLUDED_BLUEPRINTS_CSV = REPO_ROOT / "Cache/Asset/Corp/excluded_blueprints.csv"
DEFAULT_TYPES_JSON = REPO_ROOT / "Data/types.json"
CSV_EXCLUDED_KEYWORDS = ("屹立", "压缩", "末日", "旗舰", "长枪", "工业", "核心", "收割者", "力场", "投射", "抗性脚本", "现象")


def load_json(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def load_all_blueprint_ids_from_yaml(path):
    blueprint_id_pattern = re.compile(r"^(\d+):\s*$")
    blueprint_ids = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            match = blueprint_id_pattern.match(line)
            if not match:
                continue
            blueprint_ids.append(int(match.group(1)))
    return blueprint_ids


def pick_name(blueprint_type_id, types_map):
    names = types_map.get(int(blueprint_type_id), {"zh": "", "en": ""})
    if names.get("zh") or names.get("en"):
        return names.get("zh") or names.get("en")
    return str(blueprint_type_id)


def extract_t2_blueprint_ids(t2_pairs):
    t2_ids = set()
    for pair in t2_pairs:
        if isinstance(pair, (list, tuple)) and pair:
            t2_ids.add(int(pair[0]))
    return t2_ids


def load_blueprint_names(csv_path):
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return set()

    names = set()
    with csv_path.open("r", encoding="utf-8-sig") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            name_part = line.split(",", 1)[0].split("\t", 1)[0].strip()
            name_part = name_part.rstrip("*").strip()
            if name_part:
                names.add(name_part)
    return names


def build_lacked_blueprints(all_blueprint_ids, owned_blueprint_map, t2_pairs, types_map):
    owned_ids = {int(blueprint_id) for blueprint_id in owned_blueprint_map.keys()}
    t2_ids = extract_t2_blueprint_ids(t2_pairs)

    lacked = []
    for blueprint_id in all_blueprint_ids:
        if blueprint_id in owned_ids or blueprint_id in t2_ids:
            continue
        lacked.append({"id": blueprint_id, "name": pick_name(blueprint_id, types_map)})

    lacked.sort(key=lambda row: row["id"])
    return lacked


def export_blueprint_names_csv(lacked_blueprints, output_path, brought_blueprint_names=None):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    brought_blueprint_names = brought_blueprint_names or set()
    with output_path.open("w", encoding="utf-8", newline="") as f:
        for blueprint in lacked_blueprints:
            name = blueprint["name"]
            if any(keyword in name for keyword in CSV_EXCLUDED_KEYWORDS):
                continue
            if name in brought_blueprint_names:
                continue
            f.write(f"{name}\n")


def main():
    parser = argparse.ArgumentParser(description="导出缺失蓝图列表（排除已有蓝图与 T2 蓝图）")
    parser.add_argument("--all-blueprints", default=str(DEFAULT_ALL_BLUEPRINTS_YAML), help="全量蓝图 YAML 路径")
    parser.add_argument("--owned-map", default=str(DEFAULT_OWNED_BLUEPRINT_MAP), help="已拥有蓝图映射 JSON 路径")
    parser.add_argument("--t2-blueprints", default=str(DEFAULT_T2_BLUEPRINTS), help="T2 蓝图对照 JSON 路径")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 JSON 路径")
    parser.add_argument("--types-json", default=str(DEFAULT_TYPES_JSON), help="类型名映射 JSON 路径")
    parser.add_argument(
        "--names-csv-output",
        default=str(DEFAULT_NAMES_CSV_OUTPUT),
        help="仅包含蓝图名的 CSV 输出路径",
    )
    parser.add_argument(
        "--brought-blueprints-csv",
        default=str(DEFAULT_BROUGHT_BLUEPRINTS_CSV),
        help="已购买蓝图 CSV 路径（CSV 输出会排除这些蓝图）",
    )
    parser.add_argument(
        "--excluded-blueprints-csv",
        default=str(DEFAULT_EXCLUDED_BLUEPRINTS_CSV),
        help="额外排除蓝图 CSV 路径（与 Lacked_blueprints_names.csv 同格式）",
    )
    args = parser.parse_args()

    all_blueprint_ids = load_all_blueprint_ids_from_yaml(args.all_blueprints)
    owned_blueprint_map = load_json(args.owned_map)
    t2_pairs = load_json(args.t2_blueprints)
    types_map = load_types_map(args.types_json)
    brought_blueprint_names = load_blueprint_names(args.brought_blueprints_csv)
    excluded_blueprint_names = load_blueprint_names(args.excluded_blueprints_csv)
    filtered_blueprint_names = brought_blueprint_names | excluded_blueprint_names

    lacked_blueprints = build_lacked_blueprints(all_blueprint_ids, owned_blueprint_map, t2_pairs, types_map)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(lacked_blueprints, f, ensure_ascii=False, indent=2)
    export_blueprint_names_csv(
        lacked_blueprints,
        args.names_csv_output,
        brought_blueprint_names=filtered_blueprint_names,
    )

    print(f"导出完成: {output_path} (共 {len(lacked_blueprints)} 条)")
    print(f"蓝图名 CSV: {args.names_csv_output}")


if __name__ == "__main__":
    main()
