import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TYPE_MATERIALS_YAML = REPO_ROOT / "Data" / "typeMaterials.yaml"
MOON_MATERIALS_JSON = REPO_ROOT / "Data" / "Materials" / "Basic_Materials" / "moon_materials.json"
TYPES_JSON = REPO_ROOT / "Data" / "types.json"
OUTPUT_JSON = REPO_ROOT / "Data" / "Materials" / "Basic_Materials" / "moon_ore.json"

TYPE_ID_PATTERN = re.compile(r"^(\d+):\s*$")
MATERIAL_ID_PATTERN = re.compile(r"^\s*-\s*materialTypeID:\s*(\d+)\s*$")


def load_moon_material_ids(path: Path) -> set[int]:
    with path.open("r", encoding="utf-8") as f:
        moon_materials = json.load(f)
    return {int(item["id"]) for item in moon_materials}


def load_types_map(path: Path) -> dict[int, dict]:
    with path.open("r", encoding="utf-8") as f:
        types = json.load(f)
    return {int(item["id"]): item for item in types}


def extract_moon_ore_ids_from_yaml(path: Path, moon_material_ids: set[int]) -> list[int]:
    matched_ids = set()
    current_type_id = None

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            type_match = TYPE_ID_PATTERN.match(line)
            if type_match:
                current_type_id = int(type_match.group(1))
                continue

            material_match = MATERIAL_ID_PATTERN.match(line)
            if material_match and current_type_id is not None:
                material_type_id = int(material_match.group(1))
                if material_type_id in moon_material_ids:
                    matched_ids.add(current_type_id)

    return sorted(matched_ids)


def build_output(type_ids: list[int], types_map: dict[int, dict]) -> list[dict]:
    result = []
    for type_id in type_ids:
        item = types_map.get(type_id, {})
        result.append(
            {
                "id": type_id,
                "zh": item.get("zh", ""),
                "en": item.get("en", ""),
            }
        )
    return result


def main() -> None:
    moon_material_ids = load_moon_material_ids(MOON_MATERIALS_JSON)
    types_map = load_types_map(TYPES_JSON)

    moon_ore_ids = extract_moon_ore_ids_from_yaml(TYPE_MATERIALS_YAML, moon_material_ids)
    moon_ore = build_output(moon_ore_ids, types_map)

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(moon_ore, f, ensure_ascii=False, indent=2)

    print(f"导出 {len(moon_ore)} 条月矿矿石到: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
