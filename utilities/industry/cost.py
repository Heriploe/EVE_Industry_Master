"""
utilities/industry/cost.py
===========================
T2 蓝图成本、发明流程计算及 YAML 蓝图加载工具。
"""

import json
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── 路径初始化 ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from utilities.data.app_config import _find_eve_root
from utilities.blueprint.blueprint_utils import load_blueprints_from_file

# 向上查找 eve 根目录（含 config_meta.json 的目录）
_EVE_ROOT: Path = _find_eve_root(Path(__file__).resolve().parent)

# 路径常量（相对于 eve 根目录）
_DATA_DEFAULTS = {
    "types_json":       "data/types.json",
    "blueprints_yaml":  "data/blueprints.yaml",
    "t2_t1_json":       "data/T2.json",          # 即 T2.json：[[t2_bp_id, t1_bp_id], ...]
    "price_all_json":   "resources/market/price_all.json",
    "decryptor_csv":    "data/decryptor_modifier.csv",
}


def _eve_path(key: str, override: str = None) -> Path:
    """返回数据文件绝对路径。override 不为 None 时直接使用。"""
    if override:
        return Path(override)
    return _EVE_ROOT / _DATA_DEFAULTS[key]



# ---------------------------------------------------------------------------
# 类型数据加载
# ---------------------------------------------------------------------------

def _load_types_map(types_json_path=None) -> Dict[int, dict]:
    path = _eve_path("types_json", types_json_path)
    with open(path, "r", encoding="utf-8") as f:
        types_list = json.load(f)
    return {int(item["id"]): item for item in types_list if "id" in item}


# ---------------------------------------------------------------------------
# 蓝图加载（统一复用 blueprint_utils 实现）
# ---------------------------------------------------------------------------

def _load_blueprints(blueprints_yaml_path=None) -> Dict[int, dict]:
    """加载蓝图文件，复用 blueprint_utils.load_blueprints_from_file。"""
    path = _eve_path("blueprints_yaml", blueprints_yaml_path)
    return load_blueprints_from_file(path)


# ---------------------------------------------------------------------------
# T2/T1 映射（模块级缓存，避免每次调用重新读磁盘）
# ---------------------------------------------------------------------------

_T2_T1_CACHE: Optional[List[Tuple[int, int]]] = None
_T2_T1_PATH_USED: Optional[str] = None


def _load_t2_t1_pairs(t2_t1_json_path=None) -> List[Tuple[int, int]]:
    """加载 T2→T1 映射对，相同路径只读一次磁盘。"""
    global _T2_T1_CACHE, _T2_T1_PATH_USED
    path = _eve_path("t2_t1_json", t2_t1_json_path)
    path_str = str(path)
    if _T2_T1_CACHE is None or _T2_T1_PATH_USED != path_str:
        with open(path, "r", encoding="utf-8") as f:
            pairs = json.load(f)
        _T2_T1_CACHE = [
            (int(pair[0]), int(pair[1]))
            for pair in pairs
            if isinstance(pair, list) and len(pair) >= 2
        ]
        _T2_T1_PATH_USED = path_str
    return _T2_T1_CACHE


def _build_t2_to_t1_map(t2_t1_json_path=None) -> Dict[int, int]:
    """返回 {t2_blueprint_id: t1_blueprint_id} 字典。"""
    return {t2: t1 for t2, t1 in _load_t2_t1_pairs(t2_t1_json_path)}


def get_T1_from_T2(t2_blueprint_id, t2_t1_json_path=None) -> Optional[int]:
    """通过 T2_T1.json 将 T2 蓝图 ID 映射到 T1 蓝图 ID（缓存版）。"""
    mapping = _build_t2_to_t1_map(t2_t1_json_path)
    return mapping.get(int(t2_blueprint_id))


def _get_t2_from_t1(t1_blueprint_id, t2_t1_json_path=None) -> Optional[int]:
    """反查：将 T1 蓝图 ID 映射到 T2 蓝图 ID（缓存版）。"""
    t1_id = int(t1_blueprint_id)
    for t2, t1 in _load_t2_t1_pairs(t2_t1_json_path):
        if t1 == t1_id:
            return t2
    return None


# ---------------------------------------------------------------------------
# 调整价格
# ---------------------------------------------------------------------------

def _load_price_adjusted_map(price_adjusted_json_path=None) -> Dict[int, dict]:
    path = _eve_path("price_all_json", price_adjusted_json_path)
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    return {int(item["type_id"]): item for item in rows if "type_id" in item}


# ---------------------------------------------------------------------------
# 材料单价
# ---------------------------------------------------------------------------

def _get_material_unit_price(type_id, *, source, types_map, price_adjusted_map=None) -> float:
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


# ---------------------------------------------------------------------------
# 蓝图查找辅助
# ---------------------------------------------------------------------------

def _find_blueprint_by_product_id(product_id: int, blueprints: dict) -> Optional[int]:
    product_id = int(product_id)
    for bp_id, bp_data in blueprints.items():
        for activity_data in bp_data.get("activities", {}).values():
            for product in activity_data.get("products", []):
                if int(product.get("typeID", -1)) == product_id:
                    return int(bp_id)
    return None


# ---------------------------------------------------------------------------
# EIV / 基础成本
# ---------------------------------------------------------------------------

def get_base_cost(
    blueprint_id,
    activity,
    invention_product_id=None,
    blueprints_yaml_path=None,
    types_json_path=None,
    t2_t1_json_path=None,
    base_price_source=None,
    price_adjusted_json_path=None,
) -> float:
    """
    计算 EIV（materials 的 basePrice * quantity 之和）。

    - activity == "copy":      使用 manufacturing 活动材料
    - activity == "invention": 需传入 invention_product_id，按对应 T2 蓝图 manufacturing 计算
    """
    blueprint_id = int(blueprint_id)
    activity = activity.lower()

    # config loading removed — use _calc dict passed from caller
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


# ---------------------------------------------------------------------------
# 活动成本
# ---------------------------------------------------------------------------

def _get_activity_modifiers(activity: str, config) -> dict:
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
) -> float:
    """
    计算活动总花费：
      EIV = get_base_cost(...)
      JCB = EIV (manufacturing/reaction) 或 0.02 * EIV (其他活动)
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

    jcb = eiv if activity in {"manufacturing", "reaction"} else 0.02 * eiv

    # config loading removed — use _calc dict passed from caller
    modifiers = _get_activity_modifiers(activity, config)

    per_run_cost = (
        jcb
        * modifiers["system_modifier"]
        * (1 - modifiers["facility_reduction"])
        * (1 - modifiers["rig_reduction"])
        + 0.04 * jcb
    )
    return per_run_cost * runs


# ---------------------------------------------------------------------------
# 解码器
# ---------------------------------------------------------------------------

def _load_decryptor_modifiers(decryptor_modifier_csv_path=None) -> dict:
    path = (
        Path(decryptor_modifier_csv_path)
        if decryptor_modifier_csv_path
        else _eve_path("decryptor_csv")
    )
    modifiers = {}
    with open(path, "r", encoding="utf-8") as f:
        f.readline()  # 跳过表头
        for line in f:
            line = line.strip()
            if not line:
                continue
            cols = line.split()
            if len(cols) < 5:
                continue
            decryptor_id = int(cols[0])
            modifiers[decryptor_id] = {
                "probability_multiplier": float(cols[1]),
                "max_run_modifier": int(cols[2].replace("+", "")),
                "me_modifier": int(cols[3].replace("+", "")),
                "te_modifier": int(cols[4].replace("+", "")),
            }
    return modifiers


def invention_T2_runs(
    decryptor_id=None,
    decryptor_modifier_csv_path=None,
    base_success_rate=0.34,
    base_runs=1,
    base_me=0,
    base_te=0,
    invention_skill_modifier=None,
) -> Tuple[float, int, int]:
    """
    计算产出 1 单位 T2 蓝图所需的平均发明流程数，以及产出蓝图的 ME/TE 修正量。

    参数：
      base_me / base_te：基础 ME/TE（默认 0，即只返回解码器修正值）。
        若需要计算含 T2 BPC 固有基础值（EVE 中通常 ME=2, TE=4）的绝对 ME/TE，
        请显式传入 base_me=2, base_te=4（build_t2_blueprint_costs.py 中已如此调用）。

    返回 (required_invention_runs, me, te)。
    """
    modifiers = _load_decryptor_modifiers(decryptor_modifier_csv_path=decryptor_modifier_csv_path)
    decryptor = modifiers.get(int(decryptor_id), {}) if decryptor_id is not None else {}

    if invention_skill_modifier is None:
        # config loading removed — use _calc dict passed from caller
        invention_skill_modifier = config.getfloat("industry_cost", "invention_skill_modifier", fallback=1.0)

    probability_multiplier = float(decryptor.get("probability_multiplier", 1.0))
    max_run_modifier = int(decryptor.get("max_run_modifier", 0))
    me_modifier = int(decryptor.get("me_modifier", 0))
    te_modifier = int(decryptor.get("te_modifier", 0))

    modified_success_rate = float(base_success_rate) * probability_multiplier * float(invention_skill_modifier)
    modified_runs = float(base_runs) + max_run_modifier

    if modified_success_rate <= 0 or modified_runs <= 0:
        raise ValueError("修正后的成功率和流程数必须大于 0")

    required_invention_runs = 1.0 / modified_success_rate / modified_runs
    me = int(base_me) + me_modifier
    te = int(base_te) + te_modifier

    return required_invention_runs, me, te
