import json
from pathlib import Path


def load_types_map(types_json_path) -> dict[int, dict]:
    """Load Data/types.json like file into {id: {zh,en}} map."""
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
