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


def _load_types_map(types_json_path=None):
    path = Path(types_json_path) if types_json_path else _resolve_shared_path("types_json", "Data/types.json")
    with open(path, "r", encoding="utf-8") as f:
        types_list = json.load(f)
    return {int(item["id"]): item for item in types_list if "id" in item}


def _load_blueprints(blueprints_yaml_path=None):
    path = Path(blueprints_yaml_path) if blueprints_yaml_path else _resolve_shared_path("blueprints_yaml", "Data/blueprints.yaml")

    if path.suffix.lower() == ".json":
        with open(path, "r", encoding="utf-8") as f:
            blueprint_list = json.load(f)
        return {int(item["blueprintTypeID"]): {"activities": {k: v for k, v in item.items() if k in {"manufacturing", "reaction", "copying", "invention"}}} for item in blueprint_list if "blueprintTypeID" in item}

    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ModuleNotFoundError:
        fallback_path = REPO_ROOT / "Optimized Calculator/Source/blueprints_merged.json"
        with open(fallback_path, "r", encoding="utf-8") as f:
            blueprint_list = json.load(f)
        return {int(item["blueprintTypeID"]): {"activities": {k: v for k, v in item.items() if k in {"manufacturing", "reaction", "copying", "invention"}}} for item in blueprint_list if "blueprintTypeID" in item}


def _load_t2_t1_pairs(t2_t1_json_path=None):
    path = Path(t2_t1_json_path) if t2_t1_json_path else _resolve_shared_path("t2_t1_json", "Data/T2_T1.json")
    with open(path, "r", encoding="utf-8") as f:
        pairs = json.load(f)
    return [(int(pair[0]), int(pair[1])) for pair in pairs if isinstance(pair, list) and len(pair) >= 2]


def get_T2_from_T1(t1_blueprint_id, t2_t1_json_path=None):
    """通过 T2_T1.json 将 T1 蓝图ID映射到 T2 蓝图ID。"""
    t1_blueprint_id = int(t1_blueprint_id)
    for t2_blueprint_id, t1_id in _load_t2_t1_pairs(t2_t1_json_path=t2_t1_json_path):
        if t1_id == t1_blueprint_id:
            return t2_blueprint_id
    return None


def _find_blueprint_by_product_id(product_id, blueprints):
    product_id = int(product_id)
    for bp_id, bp_data in blueprints.items():
        activities = bp_data.get("activities", {})
        for activity_data in activities.values():
            for product in activity_data.get("products", []):
                if int(product.get("typeID", -1)) == product_id:
                    return int(bp_id)
    return None


def get_base_cost(blueprint_id, activity, invention_product_id=None, blueprints_yaml_path=None, types_json_path=None, t2_t1_json_path=None):
    """
    计算 EIV（materials 的 basePrice * quantity 之和）。

    - activity == "copy": 使用 manufacturing 活动材料
    - activity == "invention": 需传入 invention_product_id，按对应 T2 蓝图的 manufacturing 活动计算
    """
    blueprint_id = int(blueprint_id)
    activity = activity.lower()

    blueprints = _load_blueprints(blueprints_yaml_path=blueprints_yaml_path)
    types_map = _load_types_map(types_json_path=types_json_path)

    target_blueprint_id = blueprint_id
    target_activity = activity

    if activity == "copy":
        target_activity = "manufacturing"
    elif activity == "invention":
        if invention_product_id is None:
            raise ValueError("activity 为 invention 时，必须提供 invention_product_id")

        t2_blueprint_id = None
        if int(invention_product_id) in blueprints:
            t2_blueprint_id = int(invention_product_id)
        else:
            t2_blueprint_id = _find_blueprint_by_product_id(invention_product_id, blueprints)

        if t2_blueprint_id is None:
            t2_blueprint_id = get_T2_from_T1(blueprint_id, t2_t1_json_path=t2_t1_json_path)

        if t2_blueprint_id is None:
            raise ValueError(f"无法根据 invention_product_id={invention_product_id} 或 T2_T1 映射找到 T2 蓝图")

        target_blueprint_id = t2_blueprint_id
        target_activity = "manufacturing"

    bp_data = blueprints.get(target_blueprint_id)
    if not bp_data:
        raise KeyError(f"blueprints.yaml 中未找到蓝图 {target_blueprint_id}")

    materials = bp_data.get("activities", {}).get(target_activity, {}).get("materials", [])
    if not materials:
        return 0.0

    eiv = 0.0
    for m in materials:
        type_id = int(m["typeID"])
        quantity = float(m.get("quantity", 0))
        base_price = types_map.get(type_id, {}).get("basePrice")
        base_price = float(base_price) if isinstance(base_price, (int, float)) else 0.0
        eiv += base_price * quantity

    return eiv


def _load_industry_cost_config():
    config = configparser.ConfigParser()
    config.read(REPO_ROOT / "config.ini", encoding="utf-8")
    return config


def _get_activity_modifiers(activity, config):
    section = "industry_cost"
    activity = activity.lower()

    return {
        "system_modifier": config.getfloat(section, f"system_modifier_{activity}", fallback=1.0),
        "facility_reduction": config.getfloat(section, f"facility_reduction_{activity}", fallback=0.0),
        "rig_reduction": config.getfloat(section, f"rig_reduction_{activity}", fallback=0.0),
    }


def get_activity_cost(blueprint_id, runs, activity, invention_product_id=None, blueprints_yaml_path=None, types_json_path=None, t2_t1_json_path=None):
    """
    计算活动总花费：
      EIV = get_base_cost(...)
      JCB = 0.02 * EIV
      单流程费用 = JCB * system_modifier * (1 - facility_reduction) * (1 - rig_reduction) + 0.04 * JCB
      总费用 = 单流程费用 * runs
    """
    runs = float(runs)
    activity = activity.lower()

    eiv = get_base_cost(
        blueprint_id=blueprint_id,
        activity=activity,
        invention_product_id=invention_product_id,
        blueprints_yaml_path=blueprints_yaml_path,
        types_json_path=types_json_path,
        t2_t1_json_path=t2_t1_json_path,
    )

    jcb = 0.02 * eiv

    config = _load_industry_cost_config()
    modifiers = _get_activity_modifiers(activity, config)

    per_run_cost = (
        jcb
        * modifiers["system_modifier"]
        * (1 - modifiers["facility_reduction"])
        * (1 - modifiers["rig_reduction"])
        + 0.04 * jcb
    )
    return per_run_cost * runs
