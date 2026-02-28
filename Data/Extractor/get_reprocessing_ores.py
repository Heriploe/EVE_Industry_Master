import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ORES_JSON = REPO_ROOT / "Data" / "Materials" / "Basic_Materials" / "ores.json"
MOON_ORE_JSON = REPO_ROOT / "Data" / "Materials" / "Basic_Materials" / "moon_ore.json"
TYPES_JSON = REPO_ROOT / "Data" / "types.json"
TYPE_MATERIALS_YAML = REPO_ROOT / "Data" / "Reprocess" / "typeMaterials.yaml"
OUTPUT_JSON = REPO_ROOT / "Data" / "Reprocess" / "reprocessing_ores.json"

TYPE_ID_PATTERN = re.compile(r"^(\d+):\s*$")
MATERIAL_ID_PATTERN = re.compile(r"^\s*-\s*materialTypeID:\s*(\d+)\s*$")
QUANTITY_PATTERN = re.compile(r"^\s*quantity:\s*(\d+)\s*$")


def load_id_list(path: Path) -> list[int]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return [int(item["id"]) for item in data]


def load_types_map(path: Path) -> dict[int, dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {int(item["id"]): item for item in data}


def parse_type_materials(path: Path) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {}
    current_type_id = None
    current_material_id = None

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            m_type = TYPE_ID_PATTERN.match(line)
            if m_type:
                current_type_id = int(m_type.group(1))
                current_material_id = None
                result.setdefault(current_type_id, [])
                continue

            m_material = MATERIAL_ID_PATTERN.match(line)
            if m_material and current_type_id is not None:
                current_material_id = int(m_material.group(1))
                continue

            m_qty = QUANTITY_PATTERN.match(line)
            if m_qty and current_type_id is not None and current_material_id is not None:
                result[current_type_id].append(
                    {
                        "materialTypeID": current_material_id,
                        "quantity": int(m_qty.group(1)),
                    }
                )
                current_material_id = None

    return result


def merge_ids_in_order(*id_lists: list[int]) -> list[int]:
    seen = set()
    merged = []
    for id_list in id_lists:
        for type_id in id_list:
            if type_id not in seen:
                seen.add(type_id)
                merged.append(type_id)
    return merged


def main() -> None:
    ore_ids = load_id_list(ORES_JSON)
    moon_ore_ids = load_id_list(MOON_ORE_JSON)
    target_ids = merge_ids_in_order(ore_ids, moon_ore_ids)

    types_map = load_types_map(TYPES_JSON)
    type_materials = parse_type_materials(TYPE_MATERIALS_YAML)

    output = []
    missing_materials = []

    for type_id in target_ids:
        materials = type_materials.get(type_id)
        if not materials:
            missing_materials.append(type_id)
            continue

        names = types_map.get(type_id, {})
        output.append(
            {
                "id": type_id,
                "zh": names.get("zh", ""),
                "en": names.get("en", ""),
                "materials": materials,
            }
        )

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"目标类型数: {len(target_ids)}")
    print(f"成功导出: {len(output)} -> {OUTPUT_JSON}")
    if missing_materials:
        print(f"缺少精炼数据: {len(missing_materials)} 个（示例: {missing_materials[:10]}）")


if __name__ == "__main__":
    main()
