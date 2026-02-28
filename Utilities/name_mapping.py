import json
from pathlib import Path


def load_types_map(types_json_path) -> dict[int, dict]:
    """Load Data/types.json-like file into {id: {zh,en}} map."""
    path = Path(types_json_path)
    with path.open("r", encoding="utf-8") as f:
        items = json.load(f)

    result: dict[int, dict] = {}
    for item in items:
        tid = item.get("id")
        if tid is None:
            continue
        tid_int = int(tid)
        result[tid_int] = {
            "zh": item.get("zh", ""),
            "en": item.get("en", ""),
        }
    return result


def get_name(type_id, types_map: dict[int, dict], *, unknown_prefix: str = "未知") -> dict:
    tid = int(type_id)
    default = {"zh": f"{unknown_prefix}_{tid}", "en": f"UNKNOWN_{tid}"}
    return types_map.get(tid, default)


def id_to_name(types_map: dict[int, dict], *, lang: str = "zh", fallback_lang: str = "en") -> dict[int, str]:
    result: dict[int, str] = {}
    for tid, names in types_map.items():
        value = names.get(lang) or names.get(fallback_lang) or str(tid)
        result[int(tid)] = value
    return result


def name_to_id(types_map: dict[int, dict], *, languages: tuple[str, ...] = ("zh", "en")) -> dict[str, int]:
    result: dict[str, int] = {}
    for tid, names in types_map.items():
        for lang in languages:
            name = names.get(lang)
            if name:
                result[name] = int(tid)
    return result
