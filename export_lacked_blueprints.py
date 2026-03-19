import argparse
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_ALL_BLUEPRINTS = REPO_ROOT / "Cache/Input/blueprints_merged.json"
DEFAULT_OWNED_BLUEPRINT_MAP = REPO_ROOT / "Cache/Asset/Corp/blueprint_id_name_map.json"
DEFAULT_T2_BLUEPRINTS = REPO_ROOT / "Cache/Input/T2.json"
DEFAULT_OUTPUT = REPO_ROOT / "Cache/Asset/Corp/Lacked_blueprints.json"
DEFAULT_NAMES_CSV_OUTPUT = REPO_ROOT / "Cache/Asset/Corp/Lacked_blueprints_names.csv"


def load_json(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def pick_name(entry):
    name_obj = entry.get("name") or {}
    zh_name = name_obj.get("zh")
    en_name = name_obj.get("en")
    return zh_name or en_name or str(entry.get("blueprintTypeID"))


def extract_t2_blueprint_ids(t2_pairs):
    t2_ids = set()
    for pair in t2_pairs:
        if isinstance(pair, (list, tuple)) and pair:
            t2_ids.add(int(pair[0]))
    return t2_ids


def build_lacked_blueprints(all_blueprints, owned_blueprint_map, t2_pairs):
    owned_ids = {int(blueprint_id) for blueprint_id in owned_blueprint_map.keys()}
    t2_ids = extract_t2_blueprint_ids(t2_pairs)

    lacked = []
    for blueprint in all_blueprints:
        blueprint_id = blueprint.get("blueprintTypeID")
        if blueprint_id is None:
            continue
        if blueprint_id in owned_ids or blueprint_id in t2_ids:
            continue
        lacked.append({"id": blueprint_id, "name": pick_name(blueprint)})

    lacked.sort(key=lambda row: row["id"])
    return lacked


def export_blueprint_names_csv(lacked_blueprints, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        for blueprint in lacked_blueprints:
            f.write(f"{blueprint['name']}\n")


def main():
    parser = argparse.ArgumentParser(description="导出缺失蓝图列表（排除已有蓝图与 T2 蓝图）")
    parser.add_argument("--all-blueprints", default=str(DEFAULT_ALL_BLUEPRINTS), help="全量蓝图 JSON 路径")
    parser.add_argument("--owned-map", default=str(DEFAULT_OWNED_BLUEPRINT_MAP), help="已拥有蓝图映射 JSON 路径")
    parser.add_argument("--t2-blueprints", default=str(DEFAULT_T2_BLUEPRINTS), help="T2 蓝图对照 JSON 路径")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 JSON 路径")
    parser.add_argument(
        "--names-csv-output",
        default=str(DEFAULT_NAMES_CSV_OUTPUT),
        help="仅包含蓝图名的 CSV 输出路径",
    )
    args = parser.parse_args()

    all_blueprints = load_json(args.all_blueprints)
    owned_blueprint_map = load_json(args.owned_map)
    t2_pairs = load_json(args.t2_blueprints)

    lacked_blueprints = build_lacked_blueprints(all_blueprints, owned_blueprint_map, t2_pairs)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(lacked_blueprints, f, ensure_ascii=False, indent=2)
    export_blueprint_names_csv(lacked_blueprints, args.names_csv_output)

    print(f"导出完成: {output_path} (共 {len(lacked_blueprints)} 条)")
    print(f"蓝图名 CSV: {args.names_csv_output}")


if __name__ == "__main__":
    main()
