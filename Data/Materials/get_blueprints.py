import os
import json
import yaml

import configparser
from pathlib import Path
import sys

REPO_ROOT = next((p for p in [Path(__file__).resolve().parent, *Path(__file__).resolve().parent.parents] if (p / "config.ini").exists()), Path(__file__).resolve().parent)
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from Utilities.name_mapping import load_types_map


def _resolve_shared_path(config_key, default_rel_path):
    current_dir = Path(__file__).resolve().parent
    repo_root = next((p for p in [current_dir, *current_dir.parents] if (p / "config.ini").exists()), current_dir)

    config = configparser.ConfigParser()
    config.read(repo_root / "config.ini", encoding="utf-8")

    path_value = config.get("paths", config_key, fallback=default_rel_path)
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return str(candidate)


# 文件夹路径
source_folder = "Ships"      # 原 JSON 文件夹
output_folder = "Test"  # 输出蓝图 JSON 文件夹
blueprints_file = _resolve_shared_path("blueprints_yaml", "Data/blueprints.yaml")
types_file = _resolve_shared_path("types_json", "Data/types.json")

os.makedirs(output_folder, exist_ok=True)

# 读取 types.json
types_map = load_types_map(types_file)

# 读取 blueprints.yaml
with open(blueprints_file, "r", encoding="utf-8") as f:
    blueprints = yaml.safe_load(f)

for filename in os.listdir(source_folder):
    if not filename.endswith(".json"):
        continue

    json_path = os.path.join(source_folder, filename)
    with open(json_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    matched_blueprints = []

    for item in items:
        target_id = item["id"]
        for bp in blueprints.values():
            activities = bp.get("activities", {})

            # 获取 manufacturing 和 reaction 节点
            manufacturing_node = activities.get("manufacturing", {})
            reaction_node = activities.get("reaction", {})

            # 判断是否匹配目标 id
            def has_target(node):
                return any(p.get("typeID") == target_id for p in node.get("products", []))

            if not (has_target(manufacturing_node) or has_target(reaction_node)):
                continue

            # 构建输出蓝图
            out_bp = {
                "blueprintTypeID": bp.get("blueprintTypeID"),
                "name": types_map.get(target_id, {"zh": "", "en": ""})
            }

            # manufacturing 节点
            if manufacturing_node:
                out_bp["manufacturing"] = {}
                out_bp["manufacturing"]["materials"] = manufacturing_node.get("materials", [])
                out_bp["manufacturing"]["products"] = [
                    {
                        "quantity": p.get("quantity", 0),
                        "typeID": p.get("typeID")
                    }
                    for p in manufacturing_node.get("products", [])
                ]
                out_bp["manufacturing"]["skills"] = manufacturing_node.get("skills", [])
                out_bp["manufacturing"]["time"] = manufacturing_node.get("time", 0)

            # reaction 节点
            if reaction_node:
                out_bp["reaction"] = {}
                out_bp["reaction"]["materials"] = reaction_node.get("materials", [])
                out_bp["reaction"]["products"] = [
                    {
                        "quantity": p.get("quantity", 0),
                        "typeID": p.get("typeID")
                    }
                    for p in reaction_node.get("products", [])
                ]
                out_bp["reaction"]["skills"] = reaction_node.get("skills", [])
                out_bp["reaction"]["time"] = reaction_node.get("time", 0)

            matched_blueprints.append(out_bp)

    # 输出到指定文件夹
    output_file = os.path.join(output_folder, filename.replace(".json", "_blueprints.json"))
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(matched_blueprints, f, ensure_ascii=False, indent=2)

    print(f"{filename} -> {output_file} 导出 {len(matched_blueprints)} 条蓝图")