"""
blueprint_utils.py
==================
可复用工具：路径解析、JSON 读取、蓝图加载、物料/价格/体积查询、结果输出。
供 calculator.py 及其他脚本共同使用，避免重复代码。

兼容 Python 3.8+。
"""

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from Utilities.name_mapping import get_name as resolve_name


# ---------------------------------------------------------------------------
# 路径 & JSON
# ---------------------------------------------------------------------------

def resolve_path(config, section, key, fallback, repo_root):
    # type: (Any, str, str, str, Path) -> Path
    """从 config 读取路径，相对路径以 repo_root 为基准。"""
    value = config.get(section, key, fallback=fallback)
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate


def load_json_with_fallback(path):
    # type: (Path) -> Any
    """优先按标准 JSON 读取；失败时兼容 BOM、注释和尾随逗号。"""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        text = path.read_text(encoding="utf-8")
        text = text.lstrip("\ufeff")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
        text = re.sub(r",\s*([}\]])", r"\1", text)
        return json.loads(text)


# ---------------------------------------------------------------------------
# 蓝图加载
# ---------------------------------------------------------------------------

def get_activity(bp):
    # type: (dict) -> Tuple[Optional[dict], Optional[str]]
    """返回 (activity_data, activity_type)，优先 manufacturing，其次 reaction。"""
    if "manufacturing" in bp:
        return bp["manufacturing"], "manufacturing"
    if "reaction" in bp:
        return bp["reaction"], "reaction"
    return None, None


def _load_blueprints_by_preset(alias_file, preset_file, preset_name, repo_root):
    # type: (Path, Path, str, Path) -> Tuple[List[dict], List[dict]]
    """返回 (all_blueprints, selected_blueprints)。"""
    aliases = load_json_with_fallback(alias_file).get("aliases", [])
    presets = load_json_with_fallback(preset_file)

    alias_map = {item["alias"]: item["path"] for item in aliases}

    all_blueprints = []  # type: List[dict]
    seen_ids = set()     # type: Set[int]
    for rel in alias_map.values():
        child_data = load_json_with_fallback(repo_root / rel)
        if isinstance(child_data, list):
            for bp in child_data:
                bp_id = bp.get("blueprintTypeID")
                if bp_id is None:
                    continue
                bp_id = int(bp_id)
                if bp_id in seen_ids:
                    continue
                seen_ids.add(bp_id)
                all_blueprints.append(bp)

    preset = next((item for item in presets if item.get("name") == preset_name), None)
    if preset is None:
        raise ValueError("未找到蓝图 preset: {}".format(preset_name))

    selected = []       # type: List[dict]
    selected_ids = set()  # type: Set[int]
    for child_alias in preset.get("children", []):
        rel = alias_map.get(child_alias)
        if not rel:
            raise ValueError("蓝图 alias 不存在: {}".format(child_alias))
        child_data = load_json_with_fallback(repo_root / rel)
        if isinstance(child_data, list):
            for bp in child_data:
                bp_id = bp.get("blueprintTypeID")
                if bp_id is None:
                    continue
                bp_id = int(bp_id)
                if bp_id in selected_ids:
                    continue
                selected_ids.add(bp_id)
                selected.append(bp)

    return all_blueprints, selected


def _expand_with_recursive_deps(selected, all_blueprints):
    # type: (List[dict], List[dict]) -> List[dict]
    """将 preset 蓝图按材料依赖递归扩展，包含所有可自产的子蓝图。"""
    product_to_bps = {}  # type: Dict[int, List[int]]
    bp_by_id = {}        # type: Dict[int, dict]

    for bp in all_blueprints:
        bp_id = bp.get("blueprintTypeID")
        if bp_id is None:
            continue
        bp_id = int(bp_id)
        bp_by_id[bp_id] = bp
        activity, _ = get_activity(bp)
        if not activity:
            continue
        for product in activity.get("products", []):
            pid = product.get("typeID")
            if pid is None:
                continue
            pid = int(pid)
            if pid not in product_to_bps:
                product_to_bps[pid] = []
            product_to_bps[pid].append(bp_id)

    expanded = []   # type: List[dict]
    visited = set()  # type: Set[int]
    queue = []      # type: List[int]

    for bp in selected:
        bp_id = bp.get("blueprintTypeID")
        if bp_id is None:
            continue
        bp_id = int(bp_id)
        if bp_id in visited:
            continue
        visited.add(bp_id)
        expanded.append(bp)
        queue.append(bp_id)

    while queue:
        current_id = queue.pop(0)
        current_bp = bp_by_id.get(current_id)
        if not current_bp:
            continue
        activity, _ = get_activity(current_bp)
        if not activity:
            continue
        for material in activity.get("materials", []):
            mat_tid = material.get("typeID")
            if mat_tid is None:
                continue
            for cand_id in product_to_bps.get(int(mat_tid), []):
                if cand_id in visited:
                    continue
                cand_bp = bp_by_id.get(cand_id)
                if not cand_bp:
                    continue
                visited.add(cand_id)
                expanded.append(cand_bp)
                queue.append(cand_id)

    return expanded


def load_blueprints_for_preset(alias_file, preset_file, preset_name, repo_root):
    # type: (Path, Path, str, Path) -> Tuple[List[dict], List[dict], List[dict]]
    """
    返回 (all_blueprints, selected_blueprints, expanded_blueprints)。
    - selected: preset 中直接列出的蓝图
    - expanded: selected + 所有可自产依赖
    """
    all_bps, selected = _load_blueprints_by_preset(alias_file, preset_file, preset_name, repo_root)
    expanded = _expand_with_recursive_deps(selected, all_bps)
    return all_bps, selected, expanded


def load_ids_from_preset(alias_file, preset_file, preset_name, repo_root):
    # type: (Path, Path, str, Path) -> Set[int]
    """从 Materials 或 Blueprints preset 中读取所有 type_id 集合（id 字段）。"""
    aliases = load_json_with_fallback(alias_file).get("aliases", [])
    presets = load_json_with_fallback(preset_file)

    alias_map = {item["alias"]: item["path"] for item in aliases}
    preset = next((item for item in presets if item.get("name") == preset_name), None)
    if preset is None:
        raise ValueError("未找到 preset: {}".format(preset_name))

    result = set()  # type: Set[int]
    for child_alias in preset.get("children", []):
        rel = alias_map.get(child_alias)
        if not rel:
            raise ValueError("alias 不存在: {}".format(child_alias))
        child_data = load_json_with_fallback(repo_root / rel)
        if isinstance(child_data, list):
            for item in child_data:
                tid = item.get("id")
                if tid is not None:
                    result.add(int(tid))
    return result


def load_blueprint_type_ids_from_preset(alias_file, preset_file, preset_name, repo_root):
    # type: (Path, Path, str, Path) -> Set[int]
    """从 Blueprints preset 中读取 blueprintTypeID 集合。"""
    aliases = load_json_with_fallback(alias_file).get("aliases", [])
    presets = load_json_with_fallback(preset_file)
    alias_map = {item["alias"]: item["path"] for item in aliases}
    preset = next((item for item in presets if item.get("name") == preset_name), None)
    if preset is None:
        raise ValueError("未找到蓝图 preset: {}".format(preset_name))

    result = set()  # type: Set[int]
    for child_alias in preset.get("children", []):
        rel = alias_map.get(child_alias)
        if not rel:
            raise ValueError("alias 不存在: {}".format(child_alias))
        child_data = load_json_with_fallback(repo_root / rel)
        if isinstance(child_data, list):
            for item in child_data:
                bp_id = item.get("blueprintTypeID")
                if bp_id is not None:
                    result.add(int(bp_id))
    return result


# ---------------------------------------------------------------------------
# 库存解析
# ---------------------------------------------------------------------------

def parse_inventory(raw):
    # type: (Any) -> Dict[int, Any]
    """将多种格式的库存数据统一解析为 {type_id: quantity}。"""

    def _entry(item):
        if isinstance(item, dict):
            tid = item.get("type_id") or item.get("typeID") or item.get("id")
            qty = item.get("quantity") or item.get("qty") or item.get("count")
            if tid is None or qty is None:
                return None, None
            return int(tid), qty
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            return int(item[0]), item[1]
        return None, None

    inventory = {}  # type: Dict[int, Any]
    if isinstance(raw, dict):
        for k, v in raw.items():
            inventory[int(k)] = inventory.get(int(k), 0) + v
    elif isinstance(raw, list):
        for item in raw:
            tid, qty = _entry(item)
            if tid is not None:
                inventory[tid] = inventory.get(tid, 0) + qty
    return inventory


# ---------------------------------------------------------------------------
# 价格 & 体积
# ---------------------------------------------------------------------------

def _normalize_region_key(region_key):
    # type: (str) -> str
    """将配置中的区域名标准化为价格 JSON 中的键名。"""
    key = (region_key or "jita").strip().lower()
    alias_map = {
        "vale of the silent": "vale_of_the_silent",
        "vale_of_the_silent": "vale_of_the_silent",
        "vale of slience": "vale_of_the_silent",
        "vale_of_slience": "vale_of_the_silent",
    }
    return alias_map.get(key, key.replace(" ", "_"))


def _normalize_price_field(field):
    # type: (str) -> str
    key = (field or "buy").strip().lower()
    alias_map = {
        "buy": "lowest",
        "sell": "highest",
        "lowest": "lowest",
        "highest": "highest",
        "average": "average",
        "volume": "volume",
    }
    return alias_map.get(key, key)


def _safe_num(value):
    # type: (Any) -> float
    return value if isinstance(value, (int, float)) else 0.0


def build_prices(raw):
    # type: (Any) -> Dict[int, dict]
    """将价格数据转换为 {type_id: {region: {lowest, highest, average, volume}}}。"""
    result = {}  # type: Dict[int, dict]
    if isinstance(raw, dict):
        for k, v in raw.items():
            tid = int(k)
            result[tid] = {}
            if not isinstance(v, dict):
                continue
            for region, region_data in v.items():
                if not isinstance(region_data, dict):
                    continue
                rkey = _normalize_region_key(region)
                lowest = _safe_num(region_data.get("lowest", region_data.get("buy", 0)))
                highest = _safe_num(region_data.get("highest", region_data.get("sell", 0)))
                avg = _safe_num(region_data.get("average", 0))
                vol = _safe_num(region_data.get("volume", v.get("volume", 0)))
                result[tid][rkey] = {
                    "lowest": lowest,
                    "highest": highest,
                    "average": avg,
                    "volume": vol,
                }
        return result

    if isinstance(raw, list):
        for row in raw:
            tid = row.get("id")
            if tid is None:
                continue
            result[int(tid)] = {}
            for region, region_data in row.items():
                if region == "id" or not isinstance(region_data, dict):
                    continue
                rkey = _normalize_region_key(region)
                result[int(tid)][rkey] = {
                    "lowest": _safe_num(region_data.get("lowest", 0)),
                    "highest": _safe_num(region_data.get("highest", 0)),
                    "average": _safe_num(region_data.get("average", 0)),
                    "volume": _safe_num(region_data.get("volume", 0)),
                }
    return result


def get_price(prices, tid, region_key="jita", field="buy", fallback_region="jita"):
    # type: (dict, int, str, str, str) -> float
    region = _normalize_region_key(region_key)
    fallback = _normalize_region_key(fallback_region)
    field_key = _normalize_price_field(field)
    region_data = prices.get(int(tid), {}).get(region, {})
    val = region_data.get(field_key)
    if not isinstance(val, (int, float)) or val <= 0:
        val = prices.get(int(tid), {}).get(fallback, {}).get(field_key)
    return val if isinstance(val, (int, float)) else 0.0


def get_volume(prices, tid, region_key="jita", fallback_region="jita"):
    # type: (dict, int, str, str) -> float
    return get_price(prices, tid, region_key=region_key, field="volume", fallback_region=fallback_region)


def build_jita_prices(raw, region_key="jita"):
    # type: (Any, str) -> Dict[int, dict]
    """兼容旧接口：返回 {type_id: {buy, volume}}。"""
    region = _normalize_region_key(region_key)
    prices = build_prices(raw)
    return {
        tid: {
            "buy": get_price(prices, tid, region_key=region, field="buy"),
            "volume": get_volume(prices, tid, region_key=region),
        }
        for tid in prices
    }


def get_jita_price(jita_prices, tid, field="buy"):
    # type: (dict, int, str) -> float
    val = jita_prices.get(int(tid), {}).get(field)
    if (not isinstance(val, (int, float))) and field == "buy":
        val = jita_prices.get(int(tid), {}).get("lowest")
    if (not isinstance(val, (int, float))) and field == "sell":
        val = jita_prices.get(int(tid), {}).get("highest")
    return val if isinstance(val, (int, float)) else 0.0


def build_item_volumes(types_volume_list, ship_ids):
    # type: (list, Set[int]) -> Dict[int, float]
    """
    从 types.json 构建体积映射；船只体积除以 10 以近似打包体积。
    返回 {type_id: volume}。
    """
    result = {}  # type: Dict[int, float]
    for item in types_volume_list:
        tid = int(item.get("id", -1))
        vol = item.get("volume") or 0
        result[tid] = float(vol) / 10 if tid in ship_ids else float(vol)
    return result


def get_freight_cost(item_volumes, fare, enable_freight, tid, quantity):
    # type: (Dict[int, float], float, bool, int, Any) -> float
    if not enable_freight:
        return 0.0
    return fare * item_volumes.get(int(tid), 0) * quantity


# ---------------------------------------------------------------------------
# 利润因子
# ---------------------------------------------------------------------------

def get_product_profit_factor(tid, ship_ids, module_ids, rig_ids,
                               ship_factor, module_factor, rig_factor):
    # type: (int, Set[int], Set[int], Set[int], float, float, float) -> float
    tid = int(tid)
    if tid in ship_ids:
        return ship_factor
    if tid in module_ids:
        return module_factor
    if tid in rig_ids:
        return rig_factor
    return 1.0


# ---------------------------------------------------------------------------
# 结果输出
# ---------------------------------------------------------------------------

def compute_flow(blueprints, x_vals, purchase_vals, prod_coef, mat_coef, inventory, all_items):
    # type: (List[dict], Dict[int,int], Dict[int,int], Dict[int,dict], Dict[int,dict], Dict[int,Any], Any) -> Dict[int, dict]
    """
    计算每个物品的：total_produced, total_consumed, purchased, final_qty。
    返回 {tid: {produced, consumed, purchased, final}}。
    """
    flow = {}
    for tid in all_items:
        produced = sum(x_vals.get(i, 0) * qty for i, qty in prod_coef.get(tid, {}).items())
        consumed = sum(x_vals.get(i, 0) * qty for i, qty in mat_coef.get(tid, {}).items())
        purchased = purchase_vals.get(tid, 0)
        final = inventory.get(tid, 0) + produced - consumed + purchased
        flow[tid] = {
            "produced": produced,
            "consumed": consumed,
            "purchased": purchased,
            "final": final,
        }
    return flow


def write_purchase_csv(path, purchase_vals, jita_prices, types_map):
    # type: (Path, Dict[int,int], dict, dict) -> float
    """写入采购清单 CSV，返回总采购成本。"""
    total_cost = 0.0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        for tid, qty in sorted(purchase_vals.items()):
            qty_int = int(round(qty))
            if qty_int <= 0:
                continue
            price = get_jita_price(jita_prices, tid)
            cost = qty_int * price
            total_cost += cost
            writer.writerow([resolve_name(tid, types_map)["zh"], qty_int])
    return total_cost


def write_execution_csv(path, blueprints, x_vals, bp_score, types_map):
    # type: (Path, List[dict], Dict[int,int], dict, dict) -> None
    """写入执行清单 CSV。"""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        for i, bp in enumerate(blueprints):
            runs = int(round(x_vals.get(i, 0)))
            if runs <= 0:
                continue
            activity, _ = get_activity(bp)
            if not activity:
                continue
            bp_id = bp.get("blueprintTypeID")
            bp_name = resolve_name(bp_id, types_map)["zh"] if bp_id in types_map else "蓝图_{}".format(bp_id)
            writer.writerow([bp_name, runs])


def write_execution_csv_filtered(path, blueprints, x_vals, types_map, include_blueprint_ids):
    # type: (Path, List[dict], Dict[int,int], dict, Set[int]) -> None
    """按蓝图 ID 过滤写入执行清单 CSV。"""
    include_ids = {int(x) for x in include_blueprint_ids}
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        for i, bp in enumerate(blueprints):
            bp_id = bp.get("blueprintTypeID")
            if bp_id is None or int(bp_id) not in include_ids:
                continue
            runs = int(round(x_vals.get(i, 0)))
            if runs <= 0:
                continue
            activity, _ = get_activity(bp)
            if not activity:
                continue
            bp_name = resolve_name(bp_id, types_map)["zh"] if bp_id in types_map else "蓝图_{}".format(bp_id)
            writer.writerow([bp_name, runs])


def write_final_products_csv(path, manufactured, jita_prices, types_map):
    # type: (Path, Dict[int,int], dict, dict) -> None
    """写入最终产物清单 CSV，按总价值降序排列。"""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        for tid, qty in sorted(
            manufactured.items(),
            key=lambda kv: kv[1] * get_jita_price(jita_prices, kv[0]),
            reverse=True,
        ):
            writer.writerow([resolve_name(tid, types_map)["zh"], int(qty)])


def write_inventory_json(path, inventory_dict, types_map):
    # type: (Path, Dict[int,Any], dict) -> None
    """将 {tid: qty} 写入标准库存 JSON 格式。"""
    items = []
    for tid, qty in sorted(inventory_dict.items()):
        if qty <= 0:
            continue
        name = resolve_name(tid, types_map)
        items.append({
            "type_id": tid,
            "zh": name["zh"],
            "en": name["en"],
            "quantity": int(round(qty)),
        })
    with path.open("w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
