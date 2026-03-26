import json
import csv
import re
from pulp import LpMaximize, LpProblem, LpVariable, lpSum, LpInteger, PULP_CBC_CMD

import configparser
from pathlib import Path
import sys

REPO_ROOT = next((p for p in [Path(__file__).resolve().parent, *Path(__file__).resolve().parent.parents] if
                  (p / "config.ini").exists()), Path(__file__).resolve().parent)
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from Utilities.name_mapping import get_name as resolve_name, load_types_map


def _resolve_path(config, section, key, fallback):
    value = config.get(section, key, fallback=fallback)
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate


def _load_json_with_fallback(path: Path):
    """优先按标准 JSON 读取；失败时兼容注释和尾随逗号。"""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        text = path.read_text(encoding="utf-8")
        # 去掉 UTF-8 BOM
        text = text.lstrip("\ufeff")
        # 去掉 // 和 /* */ 注释
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
        # 去掉对象/数组中的尾随逗号
        text = re.sub(r",\s*([}\]])", r"\1", text)
        return json.loads(text)


def get_activity(bp):
    if "manufacturing" in bp:
        return bp["manufacturing"], "manufacturing"
    if "reaction" in bp:
        return bp["reaction"], "reaction"
    return None, None


def _merge_blueprints_by_preset(alias_file: Path, preset_file: Path, preset_name: str):
    all_blueprints, selected_blueprints = _load_blueprints_by_preset(alias_file, preset_file, preset_name)
    return _expand_blueprints_with_recursive_dependencies(selected_blueprints, all_blueprints)


def _load_blueprints_by_preset(alias_file: Path, preset_file: Path, preset_name: str):
    aliases = _load_json_with_fallback(alias_file).get("aliases", [])
    presets = _load_json_with_fallback(preset_file)

    alias_map = {item["alias"]: item["path"] for item in aliases}
    all_blueprints = []
    seen_blueprint_ids = set()

    for rel in alias_map.values():
        child_data = _load_json_with_fallback(REPO_ROOT / rel)
        if isinstance(child_data, list):
            for bp in child_data:
                bp_id = bp.get("blueprintTypeID")
                if bp_id is None:
                    continue
                bp_id = int(bp_id)
                if bp_id in seen_blueprint_ids:
                    continue
                seen_blueprint_ids.add(bp_id)
                all_blueprints.append(bp)

    preset = next((item for item in presets if item.get("name") == preset_name), None)
    if preset is None:
        raise ValueError(f"未找到蓝图 preset: {preset_name}")

    merged = []
    merged_ids = set()
    for child_alias in preset.get("children", []):
        rel = alias_map.get(child_alias)
        if not rel:
            raise ValueError(f"蓝图 alias 不存在: {child_alias}")
        child_data = _load_json_with_fallback(REPO_ROOT / rel)
        if isinstance(child_data, list):
            for bp in child_data:
                bp_id = bp.get("blueprintTypeID")
                if bp_id is None:
                    continue
                bp_id = int(bp_id)
                if bp_id in merged_ids:
                    continue
                merged_ids.add(bp_id)
                merged.append(bp)
    return all_blueprints, merged


def _expand_blueprints_with_recursive_dependencies(selected_blueprints, all_blueprints):
    """将 preset 蓝图按其材料递归扩展到可生产的子蓝图。"""
    product_to_blueprints = {}
    blueprint_by_id = {}

    for bp in all_blueprints:
        bp_id = bp.get("blueprintTypeID")
        if bp_id is None:
            continue
        bp_id = int(bp_id)
        blueprint_by_id[bp_id] = bp
        activity, _ = get_activity(bp)
        if not activity:
            continue
        for product in activity.get("products", []):
            product_tid = product.get("typeID")
            if product_tid is None:
                continue
            product_tid = int(product_tid)
            product_to_blueprints.setdefault(product_tid, []).append(bp_id)

    expanded = []
    visited_bp_ids = set()
    queue = []

    for bp in selected_blueprints:
        bp_id = bp.get("blueprintTypeID")
        if bp_id is None:
            continue
        bp_id = int(bp_id)
        if bp_id in visited_bp_ids:
            continue
        visited_bp_ids.add(bp_id)
        expanded.append(bp)
        queue.append(bp_id)

    while queue:
        current_bp_id = queue.pop(0)
        current_bp = blueprint_by_id.get(current_bp_id)
        if not current_bp:
            continue
        activity, _ = get_activity(current_bp)
        if not activity:
            continue

        for material in activity.get("materials", []):
            material_tid = material.get("typeID")
            if material_tid is None:
                continue
            material_tid = int(material_tid)
            candidate_bp_ids = product_to_blueprints.get(material_tid, [])
            for candidate_bp_id in candidate_bp_ids:
                if candidate_bp_id in visited_bp_ids:
                    continue
                candidate_bp = blueprint_by_id.get(candidate_bp_id)
                if not candidate_bp:
                    continue
                visited_bp_ids.add(candidate_bp_id)
                expanded.append(candidate_bp)
                queue.append(candidate_bp_id)

    return expanded


def _load_ids_from_preset(alias_file: Path, preset_file: Path, preset_name: str):
    aliases = _load_json_with_fallback(alias_file).get("aliases", [])
    presets = _load_json_with_fallback(preset_file)

    alias_map = {item["alias"]: item["path"] for item in aliases}
    preset = next((item for item in presets if item.get("name") == preset_name), None)
    if preset is None:
        raise ValueError(f"未找到 preset: {preset_name}")

    result = set()
    for child_alias in preset.get("children", []):
        rel = alias_map.get(child_alias)
        if not rel:
            raise ValueError(f"alias 不存在: {child_alias}")
        child_data = _load_json_with_fallback(REPO_ROOT / rel)
        if isinstance(child_data, list):
            for item in child_data:
                tid = item.get("id")
                if tid is not None:
                    result.add(int(tid))
    return result


config = configparser.ConfigParser()
config.read(REPO_ROOT / "config.ini", encoding="utf-8")

CALC_SECTION = "calculator_max_usage" if config.has_section("calculator_max_usage") else "calculator"

input_dir = _resolve_path(config, CALC_SECTION, "input_dir", "Cache/Input")
output_dir = _resolve_path(config, CALC_SECTION, "output_dir", "Cache/Output")
output_dir.mkdir(parents=True, exist_ok=True)

# ================== 全局参数 ==================
BUDGET = 200_000_000  # 可调整
ALPHA = 1  # 流动性权重参数
MAX_PROD_FACTOR = 1  # 单产物最大生产量占市场交易量比例
ME = 0.125
if config.has_section(CALC_SECTION):
    BUDGET = config.getint(CALC_SECTION, "budget", fallback=BUDGET)
    ALPHA = config.getfloat(CALC_SECTION, "alpha", fallback=ALPHA)
    MAX_PROD_FACTOR = config.getfloat(CALC_SECTION, "max_prod_factor", fallback=MAX_PROD_FACTOR)
    ME = config.getfloat(CALC_SECTION, "me", fallback=ME)
    FARE_JITA = config.getfloat(CALC_SECTION, "fare_jita", fallback=500)
    ENABLE_FREIGHT = config.getboolean(CALC_SECTION, "enable_freight", fallback=True)
    PURCHASE_INTEGER = config.getboolean(CALC_SECTION, "purchase_integer", fallback=False)
    SOLVER_TIME_LIMIT = config.getint(CALC_SECTION, "solver_time_limit_seconds", fallback=180)
    SOLVER_GAP_REL = config.getfloat(CALC_SECTION, "solver_gap_rel", fallback=0.005)
else:
    FARE_JITA = 500
    ENABLE_FREIGHT = True
    PURCHASE_INTEGER = False
    SOLVER_TIME_LIMIT = 180
    SOLVER_GAP_REL = 0.005

# ================== 文件 ==================
INVENTORY_JSON = _resolve_path(config, CALC_SECTION, "inventory_json", "Cache/Asset/Corp/final_non_blueprints.json")
JITA_PRICES_JSON = _resolve_path(config, CALC_SECTION, "jita_prices_json", "Cache/Input/jita_prices.json")
TYPES_JSON = _resolve_path(config, "paths", "types_json", "Data/types.json")
TYPES_VOLUME_JSON = _resolve_path(config, CALC_SECTION, "types_volume_json", str(TYPES_JSON))
BLUEPRINTS_ALIAS_JSON = _resolve_path(config, "paths", "blueprints_alias_json", "Data/Blueprints/alias.json")
BLUEPRINTS_PRESET_JSON = _resolve_path(config, "paths", "blueprints_preset_json", "Data/Blueprints/preset.json")
BLUEPRINTS_PRESET = config.get(CALC_SECTION, "blueprints_preset", fallback="items_to_sell")
MATERIALS_ALIAS_JSON = _resolve_path(config, "paths", "materials_alias_json", "Data/Materials/alias.json")
MATERIALS_PRESET_JSON = _resolve_path(config, "paths", "materials_preset_json", "Data/Materials/preset.json")

SHIPS_PRESET = config.get(CALC_SECTION, "ships_preset", fallback="ships_all")
MODULES_PRESET = config.get(CALC_SECTION, "modules_preset",
                            fallback=config.get(CALC_SECTION, "moudles_preset", fallback="modules_all"))
RIGS_PRESET = config.get(CALC_SECTION, "rigs_preset", fallback="Rigs_all")
MATERIALS_PRESET = config.get(CALC_SECTION, "materials_preset", fallback="basic")
SHIP_PROFIT_FACTOR = config.getfloat(CALC_SECTION, "ship_profit_factor", fallback=1.0)
MODULE_PROFIT_FACTOR = config.getfloat(CALC_SECTION, "module_profit_factor",
                                       fallback=config.getfloat(CALC_SECTION, "moudle_profit_factor", fallback=1.0))
RIG_PROFIT_FACTOR = config.getfloat(CALC_SECTION, "rig_profit_factor", fallback=1.0)
MATERIAL_COST_FACTOR = config.getfloat(CALC_SECTION, "material_cost_factor", fallback=1.0)

PURCHASE_CSV = output_dir / "purchase_list.csv"
EXECUTION_CSV = output_dir / "execution_list.csv"
FINAL_PRODUCTS_CSV = output_dir / "final_products.csv"
INITIAL_INVENTORY_JSON = output_dir / "initial_inventory.json"
FINAL_INVENTORY_JSON = output_dir / "final_inventory.json"

# ------------------ 读取数据 ------------------
with open(INVENTORY_JSON, "r", encoding="utf-8") as f:
    raw_inventory = json.load(f)


def _parse_inventory_entry(item):
    if isinstance(item, dict):
        tid = item.get("type_id")
        if tid is None:
            tid = item.get("typeID")
        if tid is None:
            tid = item.get("id")

        qty = item.get("quantity")
        if qty is None:
            qty = item.get("qty")
        if qty is None:
            qty = item.get("count")

        if tid is None or qty is None:
            return None, None
        return int(tid), qty

    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[0]), item[1]

    return None, None


inventory = {}
if isinstance(raw_inventory, dict):
    # 字典格式也可能有重复，需要累加
    for k, v in raw_inventory.items():
        tid = int(k)
        inventory[tid] = inventory.get(tid, 0) + v
elif isinstance(raw_inventory, list):
    for item in raw_inventory:
        tid, qty = _parse_inventory_entry(item)
        if tid is None:
            continue
        inventory[tid] = inventory.get(tid, 0) + qty

blueprints = _merge_blueprints_by_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, BLUEPRINTS_PRESET)

with JITA_PRICES_JSON.open("r", encoding="utf-8") as f:
    jita_prices_raw = json.load(f)
jita_prices = {}
for k, v in jita_prices_raw.items():
    jita_prices[int(k)] = {
        "buy": v["jita"].get("buy") if isinstance(v["jita"].get("buy"), (int, float)) else 0,
        "volume": v["jita"].get("volume", 0) if isinstance(v.get("volume", 0), (int, float)) else 0
    }

# ------------------ 读取 types.json ------------------
types_map = load_types_map(TYPES_JSON)

# ------------------ 读取 types.json 体积数据 ------------------
with TYPES_VOLUME_JSON.open("r", encoding="utf-8") as f:
    types_volume_list = json.load(f)

# 创建物品ID到体积的映射
item_volumes = {int(item["id"]): (item.get("volume") or 0) for item in types_volume_list}

# ------------------ 读取各类 preset ------------------
ship_ids = _load_ids_from_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, SHIPS_PRESET)
module_product_ids = _load_ids_from_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, MODULES_PRESET)
rig_product_ids = _load_ids_from_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, RIGS_PRESET)
basic_material_ids = _load_ids_from_preset(MATERIALS_ALIAS_JSON, MATERIALS_PRESET_JSON, MATERIALS_PRESET)


# ------------------ 工具函数 ------------------
def get_jita_price(tid, field="buy"):
    item = jita_prices.get(int(tid), {})
    val = item.get(field)
    if isinstance(val, (int, float)):
        return val
    return 0


def get_price(tid, field="buy"):
    return get_jita_price(tid, field)


def get_product_profit_factor(product_type_id):
    tid = int(product_type_id)
    if tid in ship_ids:
        return SHIP_PROFIT_FACTOR
    if tid in module_product_ids:
        return MODULE_PROFIT_FACTOR
    if tid in rig_product_ids:
        return RIG_PROFIT_FACTOR
    return 1.0


def get_material_cost_factor(material_type_id):
    return MATERIAL_COST_FACTOR if int(material_type_id) in basic_material_ids else 1.0


def get_volume(tid):
    """获取物品体积，如果是船只则除以10"""
    volume = item_volumes.get(int(tid), 0)
    if int(tid) in ship_ids:
        volume = volume / 10
    return volume


def get_freight_cost(tid, quantity):
    """计算运费成本：单位运费 × 体积 × 数量（可开关）"""
    if not ENABLE_FREIGHT:
        return 0
    volume = get_volume(tid)
    return FARE_JITA * volume * quantity


# 建立蓝图ID → 名称映射
bp_names = {}
for bp in blueprints:
    bp_id = bp.get("blueprintTypeID")
    if bp_id:
        bp_names[bp_id] = resolve_name(bp_id, types_map) if bp_id in types_map else {"zh": f"蓝图_{bp_id}",
                                                                                     "en": f"BP_{bp_id}"}

# ------------------ 建立 ILP ------------------
model = LpProblem("Max_Inventory_Usage_Value", LpMaximize)

# ------------------ 物料集合 ------------------
all_items = set()
material_items = set()
product_items = set()

for bp in blueprints:
    activity, _ = get_activity(bp)
    if not activity:
        continue
    for m in activity.get("materials", []):
        tid = m["typeID"]
        all_items.add(tid)
        material_items.add(tid)
    for p in activity.get("products", []):
        tid = p["typeID"]
        all_items.add(tid)
        product_items.add(tid)

print(f"总物品数: {len(all_items)}")
print(f"材料物品数: {len(material_items)}")
print(f"产物物品数: {len(product_items)}")

purchase_cat = LpInteger if PURCHASE_INTEGER else "Continuous"
purchase = {tid: LpVariable(f"buy_{tid}", lowBound=0, cat=purchase_cat) for tid in material_items}

# ------------------ 计算蓝图最大可生产次数 ------------------
bp_max_runs = []
for i, bp in enumerate(blueprints):
    activity, _ = get_activity(bp)
    if not activity:
        bp_max_runs.append(0)
        continue
    products = activity.get("products", [])
    if not products:
        bp_max_runs.append(0)
        continue
    # 默认只取第一个产物计算上限
    p = products[0]
    tid = p["typeID"]
    qty_per_run = p.get("quantity", 1)
    market_vol = jita_prices.get(tid, {}).get("volume", 0)
    max_runs = int((market_vol * MAX_PROD_FACTOR) / qty_per_run) if qty_per_run > 0 else 0
    bp_max_runs.append(max(max_runs, 0))  # 确保非负

x = {i: LpVariable(f"bp_{i}", lowBound=0, upBound=bp_max_runs[i], cat=LpInteger)
     for i in range(len(blueprints))}

# ------------------ 计算总权重（Jita销量 * Jita价格） ------------------
total_market_weight = 0
for tid in all_items:
    price = get_price(tid, "buy")
    volume = jita_prices.get(tid, {}).get("volume", 0)
    total_market_weight += max(price * volume, 0)

# ------------------ 计算蓝图评分：最大化库存消耗价值（考虑权重） ------------------
bp_score = {}

for i, bp in enumerate(blueprints):
    activity, _ = get_activity(bp)
    if not activity:
        bp_score[i] = 0
        continue

    # 库存利用价值：材料消耗量 * Jita价格
    materials_usage_value = sum(
        m.get("quantity", 0) * get_jita_price(m["typeID"], "buy")
        for m in activity.get("materials", [])
    )

    # 权重：Jita销量 * Jita价格，按材料聚合并标准化
    material_weight = sum(
        m.get("quantity", 0)
        * get_jita_price(m["typeID"], "buy")
        * jita_prices.get(m["typeID"], {}).get("volume", 0)
        for m in activity.get("materials", [])
    )
    normalized_weight = material_weight / total_market_weight if total_market_weight > 0 else 0
    bp_score[i] = materials_usage_value * (1 + ALPHA * normalized_weight)

# ------------------ 预计算系数（减少重复表达式构造） ------------------
mat_coef = {tid: {} for tid in all_items}
prod_coef = {tid: {} for tid in all_items}
for i, bp in enumerate(blueprints):
    activity, _ = get_activity(bp)
    if not activity:
        continue
    for m in activity.get("materials", []):
        mat_coef[m["typeID"]][i] = m.get("quantity", 0)
    for p in activity.get("products", []):
        prod_coef[p["typeID"]][i] = p.get("quantity", 0)

# ------------------ 目标函数 ------------------
model += lpSum(bp_score[i] * x[i] for i in range(len(blueprints)))

# ------------------ 物料约束（递归利用产物） ------------------
for tid in all_items:
    total_needed = lpSum(
        x[i] * qty for i, qty in mat_coef[tid].items()
    )
    total_produced = lpSum(
        x[i] * qty for i, qty in prod_coef[tid].items()
    )
    purchased = purchase.get(tid, 0)
    model += (inventory.get(tid, 0) + purchased + total_produced >= total_needed)

# ------------------ 预算约束 ------------------
model += lpSum(purchase[tid] * get_jita_price(tid, "buy") for tid in material_items) <= BUDGET

# ------------------ 求解 ------------------
print("开始求解...")
solver = PULP_CBC_CMD(msg=True, timeLimit=SOLVER_TIME_LIMIT, gapRel=SOLVER_GAP_REL)
model.solve(solver)
print(f"求解状态: {model.status}")

if model.status == 1:  # Optimal
    total_usage_score = sum(bp_score[i] * x[i].value() for i in range(len(blueprints)) if x[i].value() > 0)

    # 计算制造的产物（仅包含制造的物品）和最终库存（所有物品）
    manufactured_products = {}  # 仅制造的产物
    final_inventory_dict = {}  # 完整的最终库存

    for tid in all_items:
        total_produced = sum(
            (int(x[i].value()) if x[i].value() else 0) *
            next((p["quantity"] for p in get_activity(bp)[0].get("products", [])
                  if p["typeID"] == tid), 0)
            for i, bp in enumerate(blueprints)
            if get_activity(bp)[0] is not None
        )
        total_consumed = sum(
            (int(x[i].value()) if x[i].value() else 0) *
            next((m["quantity"] for m in get_activity(bp)[0].get("materials", [])
                  if m["typeID"] == tid), 0)
            for i, bp in enumerate(blueprints)
            if get_activity(bp)[0] is not None
        )
        purchased_var = purchase.get(tid)
        purchased = int(purchased_var.value()) if purchased_var is not None and purchased_var.value() else 0

        # 最终库存 = 初始库存 + 生产 - 消耗 + 采购
        final_qty = inventory.get(tid, 0) + total_produced - total_consumed + purchased
        if final_qty > 0:
            final_inventory_dict[tid] = final_qty

        # 制造产物 = 仅包含有生产量的物品
        if total_produced > 0:
            manufactured_products[tid] = total_produced

    final_product_count = len(manufactured_products)

    print(f"库存利用目标值: {total_usage_score:,.0f}")
    print(f"最终产物种类数: {final_product_count}")

    # ------------------ 输出中文采购清单 CSV ------------------
    with open(PURCHASE_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        total_purchase_cost = 0
        for tid, var in purchase.items():
            qty = int(round(var.value())) if var.value() else 0
            if qty > 0:
                price = get_jita_price(tid, "buy")
                total_cost = qty * price
                total_purchase_cost += total_cost
                writer.writerow([resolve_name(tid, types_map)["zh"], qty])

    # ------------------ 输出中文执行清单 CSV ------------------
    with open(EXECUTION_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        for i, bp in enumerate(blueprints):
            runs = int(x[i].value()) if x[i].value() else 0
            if runs > 0:
                activity, _ = get_activity(bp)
                if not activity:
                    continue

                # 直接获取蓝图名称
                bp_id = bp.get("blueprintTypeID")
                bp_name = resolve_name(bp_id, types_map)["zh"] if bp_id in types_map else f"蓝图_{bp_id}"

                # 获取产物名称
                products = activity.get("products", [])
                product_names = ", ".join([resolve_name(p["typeID"], types_map)["zh"] for p in products])

                # 计算该蓝图的利润
                profit = bp_score[i] * runs

                writer.writerow([bp_name, runs])

    # ------------------ 输出最终产物总量 CSV ------------------
    with open(FINAL_PRODUCTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        total_value = 0
        for tid, qty in sorted(manufactured_products.items(), key=lambda x: x[1] * get_price(x[0], "buy"),
                               reverse=True):
            price = get_price(tid, "buy")
            value = qty * price
            total_value += value
            writer.writerow([resolve_name(tid, types_map)["zh"], qty])

    print(f"完成：采购清单 → {PURCHASE_CSV}")
    print(f"完成：执行清单 → {EXECUTION_CSV}")
    print(f"完成：最终产物总量 → {FINAL_PRODUCTS_CSV}")

    # ------------------ 合并材料库存和采购清单为 initial_inventory.json ------------------
    # 创建合并后的库存字典
    merged_inventory = {}

    # 添加原材料库存
    for tid, qty in inventory.items():
        merged_inventory[tid] = qty

    # 添加采购清单
    for tid, var in purchase.items():
        qty = int(round(var.value())) if var.value() else 0
        if qty > 0:
            merged_inventory[tid] = merged_inventory.get(tid, 0) + qty

    # 转换为列表格式（与materials.json格式一致）
    initial_inventory_list = []
    for tid, qty in sorted(merged_inventory.items()):
        if qty > 0:
            initial_inventory_list.append({
                "type_id": tid,
                "zh": resolve_name(tid, types_map)["zh"],
                "en": resolve_name(tid, types_map)["en"],
                "quantity": qty
            })

    # 写入JSON文件
    with open(INITIAL_INVENTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(initial_inventory_list, f, ensure_ascii=False, indent=2)

    print(f"完成：初始库存 → {INITIAL_INVENTORY_JSON}")

    # ------------------ 输出最终库存为 final_inventory.json ------------------
    # 转换为列表格式（与materials.json格式一致）
    final_inventory_list = []
    for tid, qty in sorted(final_inventory_dict.items()):
        if qty > 0:
            final_inventory_list.append({
                "type_id": tid,
                "zh": resolve_name(tid, types_map)["zh"],
                "en": resolve_name(tid, types_map)["en"],
                "quantity": qty
            })

    # 写入JSON文件
    with open(FINAL_INVENTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(final_inventory_list, f, ensure_ascii=False, indent=2)

    print(f"完成：最终库存 → {FINAL_INVENTORY_JSON}")

else:
    print("求解失败或无可行解")
