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


def _load_t2_t1_pairs(t2_t1_json_path=None):
    path = Path(t2_t1_json_path) if t2_t1_json_path else _resolve_shared_path("t2_t1_json", "Data/T2_T1.json")
    with open(path, "r", encoding="utf-8") as f:
        pairs = json.load(f)
    return [(int(pair[0]), int(pair[1])) for pair in pairs if isinstance(pair, list) and len(pair) >= 2]


def _load_price_adjusted_map(price_adjusted_json_path=None):
    path = Path(price_adjusted_json_path) if price_adjusted_json_path else _resolve_shared_path("price_adjusted_json", "Data/price_adjusted.json")
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    return {int(item["type_id"]): item for item in rows if "type_id" in item}


def _get_material_unit_price(type_id, *, source, types_map, price_adjusted_map=None):
    source = source.lower()
    if source == "types_base":
        value = types_map.get(type_id, {}).get("basePrice")
    elif source == "adjusted_price":
        value = (price_adjusted_map or {}).get(type_id, {}).get("adjusted_price")
    elif source == "average_price":
        value = (price_adjusted_map or {}).get(type_id, {}).get("average_price")
    else:
        raise ValueError("base_price_source 必须是 adjusted_price、average_price 或 types_base")

    return float(value) if isinstance(value, (int, float)) else 0.0


def get_T1_from_T2(t2_blueprint_id, t2_t1_json_path=None):
    """通过 T2_T1.json 将 T2 蓝图ID映射到 T1 蓝图ID。"""
    t2_blueprint_id = int(t2_blueprint_id)
    for t2_id, t1_blueprint_id in _load_t2_t1_pairs(t2_t1_json_path=t2_t1_json_path):
        if t2_id == t2_blueprint_id:
            return t1_blueprint_id
    return None


def _get_t2_from_t1(t1_blueprint_id, t2_t1_json_path=None):
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


def get_base_cost(
    blueprint_id,
    activity,
    invention_product_id=None,
    blueprints_yaml_path=None,
    types_json_path=None,
    t2_t1_json_path=None,
    base_price_source=None,
    price_adjusted_json_path=None,
):
    """
    计算 EIV（materials 的 basePrice * quantity 之和）。

    - activity == "copy": 使用 manufacturing 活动材料
    - activity == "invention": 需传入 invention_product_id，按对应 T2 蓝图的 manufacturing 活动计算
    - base_price_source:
      - "types_base"：使用 types.json 的 basePrice
      - "adjusted_price"：使用 price_adjusted.json 的 adjusted_price
      - "average_price"：使用 price_adjusted.json 的 average_price
    """
    blueprint_id = int(blueprint_id)
    activity = activity.lower()

    config = _load_industry_cost_config()
    base_price_source = (base_price_source or config.get("industry_cost", "base_price_source", fallback="types_base")).lower()

    blueprints = _load_blueprints(blueprints_yaml_path=blueprints_yaml_path)
    types_map = _load_types_map(types_json_path=types_json_path)
    price_adjusted_map = None
    if base_price_source in {"adjusted_price", "average_price"}:
        price_adjusted_map = _load_price_adjusted_map(price_adjusted_json_path=price_adjusted_json_path)

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
            t2_blueprint_id = _get_t2_from_t1(blueprint_id, t2_t1_json_path=t2_t1_json_path)

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
    for material in materials:
        type_id = int(material["typeID"])
        quantity = float(material.get("quantity", 0))
        base_price = _get_material_unit_price(
            type_id,
            source=base_price_source,
            types_map=types_map,
            price_adjusted_map=price_adjusted_map,
        )
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


def get_activity_cost(
    blueprint_id,
    runs,
    activity,
    invention_product_id=None,
    blueprints_yaml_path=None,
    types_json_path=None,
    t2_t1_json_path=None,
    base_price_source=None,
    price_adjusted_json_path=None,
):
    """
    计算活动总花费：
      EIV = get_base_cost(...)
      JCB = EIV (manufacturing) 或 0.02 * EIV (其他活动)
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
        base_price_source=base_price_source,
        price_adjusted_json_path=price_adjusted_json_path,
    )

    if activity == "manufacturing" or activity == "reaction":
        jcb = eiv
    else:
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


def _load_decryptor_modifiers(decryptor_modifier_csv_path=None):
    path = (
        Path(decryptor_modifier_csv_path)
        if decryptor_modifier_csv_path
        else REPO_ROOT / "Data/decryptor_modifier.csv"
    )

    modifiers = {}
    with open(path, "r", encoding="utf-8") as f:
        header = f.readline()
        for line in f:
            line = line.strip()
            if not line:
                continue
            cols = line.split()
            if len(cols) < 5:
                continue

            decryptor_id = int(cols[0])
            probability_multiplier = float(cols[1])
            max_run_modifier = int(cols[2].replace("+", ""))
            me_modifier = int(cols[3].replace("+", ""))
            te_modifier = int(cols[4].replace("+", ""))

            modifiers[decryptor_id] = {
                "probability_multiplier": probability_multiplier,
                "max_run_modifier": max_run_modifier,
                "me_modifier": me_modifier,
                "te_modifier": te_modifier,
            }

    return modifiers


def invention_T2_runs(
    decryptor_id=None,
    decryptor_modifier_csv_path=None,
    base_success_rate=0.34,
    base_runs=1,
    base_me=0,
    base_te=0,
):
    """
    计算产出 1 单位 T2 蓝图所需的平均发明流程数，以及产出蓝图的 ME/TE。

    公式：
      modified_success_rate = base_success_rate * probability_multiplier
      modified_runs = base_runs + max_run_modifier
      required_invention_runs = 1 / modified_success_rate / modified_runs

    若 decryptor_id 为空或不在 decryptor_modifier.csv 中，则按无修正处理。
    """
    modifiers = _load_decryptor_modifiers(decryptor_modifier_csv_path=decryptor_modifier_csv_path)
    decryptor = modifiers.get(int(decryptor_id), {}) if decryptor_id is not None else {}

    probability_multiplier = float(decryptor.get("probability_multiplier", 1.0))
    max_run_modifier = int(decryptor.get("max_run_modifier", 0))
    me_modifier = int(decryptor.get("me_modifier", 0))
    te_modifier = int(decryptor.get("te_modifier", 0))

    modified_success_rate = float(base_success_rate) * probability_multiplier
    modified_runs = float(base_runs) + max_run_modifier

    if modified_success_rate <= 0 or modified_runs <= 0:
        raise ValueError("修正后的成功率和流程数必须大于 0")

    required_invention_runs = 1.0 / modified_success_rate / modified_runs
    me = int(base_me) + me_modifier
    te = int(base_te) + te_modifier

    return required_invention_runs, me, te
