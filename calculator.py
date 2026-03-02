import json
import csv
from pulp import LpMaximize, LpProblem, LpVariable, lpSum, LpInteger, LpBinary

import configparser
from pathlib import Path
import sys

REPO_ROOT = next((p for p in [Path(__file__).resolve().parent, *Path(__file__).resolve().parent.parents] if (p / "config.ini").exists()), Path(__file__).resolve().parent)
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from Utilities.name_mapping import get_name as resolve_name, load_types_map


def _resolve_path(config, section, key, fallback):
    value = config.get(section, key, fallback=fallback)
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate


def _merge_blueprints_by_preset(alias_file: Path, preset_file: Path, preset_name: str):
    with alias_file.open("r", encoding="utf-8") as f:
        aliases = json.load(f).get("aliases", [])
    with preset_file.open("r", encoding="utf-8") as f:
        presets = json.load(f)

    alias_map = {item["alias"]: item["path"] for item in aliases}
    preset = next((item for item in presets if item.get("name") == preset_name), None)
    if preset is None:
        raise ValueError(f"未找到蓝图 preset: {preset_name}")

    merged = []
    for child_alias in preset.get("children", []):
        rel = alias_map.get(child_alias)
        if not rel:
            raise ValueError(f"蓝图 alias 不存在: {child_alias}")
        with (REPO_ROOT / rel).open("r", encoding="utf-8") as f:
            child_data = json.load(f)
            if isinstance(child_data, list):
                merged.extend(child_data)
    return merged

def _load_ids_from_preset(alias_file: Path, preset_file: Path, preset_name: str):
    with alias_file.open("r", encoding="utf-8") as f:
        aliases = json.load(f).get("aliases", [])
    with preset_file.open("r", encoding="utf-8") as f:
        presets = json.load(f)

    alias_map = {item["alias"]: item["path"] for item in aliases}
    preset = next((item for item in presets if item.get("name") == preset_name), None)
    if preset is None:
        raise ValueError(f"未找到 preset: {preset_name}")

    result = set()
    for child_alias in preset.get("children", []):
        rel = alias_map.get(child_alias)
        if not rel:
            raise ValueError(f"alias 不存在: {child_alias}")
        with (REPO_ROOT / rel).open("r", encoding="utf-8") as f:
            child_data = json.load(f)
            if isinstance(child_data, list):
                for item in child_data:
                    tid = item.get("id")
                    if tid is not None:
                        result.add(int(tid))
    return result


config = configparser.ConfigParser()
config.read(REPO_ROOT / "config.ini", encoding="utf-8")

input_dir = _resolve_path(config, "calculator", "input_dir", "Cache/Input")
output_dir = _resolve_path(config, "calculator", "output_dir", "Cache/Output")
output_dir.mkdir(parents=True, exist_ok=True)

# ================== 全局参数 ==================
BUDGET = 200_000_000  # 可调整
ALPHA = 1  # 流动性权重参数
MAX_PROD_FACTOR = 1  # 单产物最大生产量占市场交易量比例
ME = 0.125
if config.has_section("calculator"):
    BUDGET = config.getint("calculator", "budget", fallback=BUDGET)
    ALPHA = config.getfloat("calculator", "alpha", fallback=ALPHA)
    MAX_PROD_FACTOR = config.getfloat("calculator", "max_prod_factor", fallback=MAX_PROD_FACTOR)
    ME = config.getfloat("calculator", "me", fallback=ME)
    PRODUCT_DIVERSITY_PENALTY = config.getfloat("calculator", "product_diversity_penalty", fallback=0)
    FARE_JITA = config.getfloat("calculator", "fare_jita", fallback=500)
else:
    PRODUCT_DIVERSITY_PENALTY = 0
    FARE_JITA = 500

# ================== 文件 ==================
INVENTORY_JSON = _resolve_path(config, "calculator", "inventory_json", "Cache/Asset/Corp/final_non_blueprints.json")
JITA_PRICES_JSON = _resolve_path(config, "calculator", "jita_prices_json", "Cache/Input/jita_prices.json")
TYPES_JSON = _resolve_path(config, "paths", "types_json", "Data/types.json")
TYPES_VOLUME_JSON = _resolve_path(config, "calculator", "types_volume_json", str(TYPES_JSON))
BLUEPRINTS_ALIAS_JSON = _resolve_path(config, "paths", "blueprints_alias_json", "Data/Blueprints/alias.json")
BLUEPRINTS_PRESET_JSON = _resolve_path(config, "paths", "blueprints_preset_json", "Data/Blueprints/preset.json")
BLUEPRINTS_PRESET = config.get("calculator", "blueprints_preset", fallback="all")
MATERIALS_ALIAS_JSON = _resolve_path(config, "paths", "materials_alias_json", "Data/Materials/alias.json")
MATERIALS_PRESET_JSON = _resolve_path(config, "paths", "materials_preset_json", "Data/Materials/preset.json")

SHIPS_PRESET = config.get("calculator", "ships_preset", fallback="ships_all")
MOUDLES_PRESET = config.get("calculator", "moudles_preset", fallback="moudles_all")
RIGS_PRESET = config.get("calculator", "rigs_preset", fallback="Rigs_all")
MATERIALS_PRESET = config.get("calculator", "materials_preset", fallback="basic")
SHIP_PROFIT_FACTOR = config.getfloat("calculator", "ship_profit_factor", fallback=1.0)
MOUDLE_PROFIT_FACTOR = config.getfloat("calculator", "moudle_profit_factor", fallback=1.0)
RIG_PROFIT_FACTOR = config.getfloat("calculator", "rig_profit_factor", fallback=1.0)
MATERIAL_COST_FACTOR = config.getfloat("calculator", "material_cost_factor", fallback=1.0)

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
moudle_product_ids = _load_ids_from_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, MOUDLES_PRESET)
rig_product_ids = _load_ids_from_preset(BLUEPRINTS_ALIAS_JSON, BLUEPRINTS_PRESET_JSON, RIGS_PRESET)
basic_material_ids = _load_ids_from_preset(MATERIALS_ALIAS_JSON, MATERIALS_PRESET_JSON, MATERIALS_PRESET)

# ------------------ 工具函数 ------------------
def get_activity(bp):
    if "manufacturing" in bp:
        return bp["manufacturing"], "manufacturing"
    if "reaction" in bp:
        return bp["reaction"], "reaction"
    return None, None



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
    if tid in moudle_product_ids:
        return MOUDLE_PROFIT_FACTOR
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
    """计算运费成本：单位运费 × 体积 × 数量"""
    volume = get_volume(tid)
    return FARE_JITA * volume * quantity


# 建立蓝图ID → 名称映射
bp_names = {}
for bp in blueprints:
    bp_id = bp.get("blueprintTypeID")
    if bp_id:
        bp_names[bp_id] = resolve_name(bp_id, types_map) if bp_id in types_map else {"zh": f"蓝图_{bp_id}", "en": f"BP_{bp_id}"}

# ------------------ 建立 ILP ------------------
model = LpProblem("Max_Profit_Min_Products", LpMaximize)

# ------------------ 物料集合 ------------------
all_items = set()

for bp in blueprints:
    activity, _ = get_activity(bp)
    if not activity:
        continue
    for m in activity.get("materials", []):
        all_items.add(m["typeID"])
    for p in activity.get("products", []):
        all_items.add(p["typeID"])

print(f"总物品数: {len(all_items)}")

purchase = {tid: LpVariable(f"buy_{tid}", lowBound=0, cat=LpInteger) for tid in all_items}

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

# ------------------ 引入二元变量：物品是否有净产出 ------------------
# 关键修复：y[tid] = 1 表示该物品有净产出（可以出售）
# 不再预先排除任何物品，而是在求解后根据净产出判断
y = {tid: LpVariable(f"has_output_{tid}", cat=LpBinary) for tid in all_items}

# 大M法：如果某个物品的净产出 > 0，则 y[tid] = 1
M = 1e9  # 一个足够大的数

# ------------------ 计算总市场价值（sell_price * volume） ------------------
total_market_value = 0
for tid in all_items:
    sell = get_price(tid, "buy")
    vol = jita_prices.get(tid, {}).get("volume", 0)
    total_market_value += sell * vol

# ------------------ 计算蓝图评分 ------------------
bp_score = {}

for i, bp in enumerate(blueprints):
    activity, act_type = get_activity(bp)
    if not activity:
        bp_score[i] = 0
        continue

    # 材料成本（统一从Jita购买）
    materials_cost = sum(
        m.get("quantity", 0) * get_jita_price(m["typeID"], "buy") * get_material_cost_factor(m["typeID"])
        for m in activity.get("materials", [])
    )
    
    jita_products_value = sum(
        p.get("quantity", 0) * get_jita_price(p["typeID"], "buy")
        for p in activity.get("products", [])
    )
    jita_freight_cost = sum(
        FARE_JITA * get_volume(p["typeID"]) * p.get("quantity", 0)
        for p in activity.get("products", [])
    )

    if act_type == "manufacturing":
        jita_profit = jita_products_value - materials_cost * (1 - ME) - jita_freight_cost
    else:
        jita_profit = jita_products_value - materials_cost - jita_freight_cost

    product_factor = 1.0
    products = activity.get("products", [])
    if products:
        product_factor = get_product_profit_factor(products[0]["typeID"])
    profit = jita_profit * product_factor
    
    # 流动性系数始终以Jita为准
    bp_market_value = sum(
        p.get("quantity", 0) * get_jita_price(p["typeID"], "buy") *
        jita_prices.get(p["typeID"], {}).get("volume", 0)
        for p in activity.get("products", [])
    )
    liq_weight = bp_market_value / total_market_value if total_market_value > 0 else 0
    profit_score = profit * (1 + ALPHA * liq_weight)
    bp_score[i] = profit_score

# ------------------ 目标函数 ------------------
# 修复：最终产物种类数 = 所有有净产出的物品数量
model += (
        lpSum(bp_score[i] * x[i] for i in range(len(blueprints)))
        - PRODUCT_DIVERSITY_PENALTY * lpSum(y[tid] for tid in all_items)
)

# ------------------ 物料约束（递归利用产物） ------------------
for tid in all_items:
    total_needed = lpSum(
        x[i] * next((m["quantity"] for m in get_activity(bp)[0].get("materials", [])
                     if m["typeID"] == tid), 0)
        for i, bp in enumerate(blueprints)
        if get_activity(bp)[0] is not None
    )
    total_produced = lpSum(
        x[i] * next((p["quantity"] for p in get_activity(bp)[0].get("products", [])
                     if p["typeID"] == tid), 0)
        for i, bp in enumerate(blueprints)
        if get_activity(bp)[0] is not None
    )
    model += (inventory.get(tid, 0) + purchase[tid] + total_produced >= total_needed)

# ------------------ 最终产物约束（修复）------------------
# 关键修复：对所有物品，如果净产出 > 0，则 y[tid] = 1
for tid in all_items:
    total_produced = lpSum(
        x[i] * next((p["quantity"] for p in get_activity(bp)[0].get("products", [])
                     if p["typeID"] == tid), 0)
        for i, bp in enumerate(blueprints)
        if get_activity(bp)[0] is not None
    )
    total_consumed = lpSum(
        x[i] * next((m["quantity"] for m in get_activity(bp)[0].get("materials", [])
                     if m["typeID"] == tid), 0)
        for i, bp in enumerate(blueprints)
        if get_activity(bp)[0] is not None
    )
    purchased = purchase[tid]

    # 净产出 = 库存 + 生产 - 消耗 + 采购
    net_output = inventory.get(tid, 0) + total_produced - total_consumed + purchased

    # 如果 net_output > 0，则强制 y[tid] = 1
    model += (net_output <= M * y[tid])

# ------------------ 预算约束 ------------------
model += lpSum(purchase[tid] * get_jita_price(tid, "buy") for tid in all_items) <= BUDGET

# ------------------ 求解 ------------------
print("开始求解...")
model.solve()
print(f"求解状态: {model.status}")

if model.status == 1:  # Optimal
    total_profit = sum(bp_score[i] * x[i].value() for i in range(len(blueprints)) if x[i].value() > 0)

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
        purchased = int(purchase[tid].value()) if purchase[tid].value() else 0

        # 最终库存 = 初始库存 + 生产 - 消耗 + 采购
        final_qty = inventory.get(tid, 0) + total_produced - total_consumed + purchased
        if final_qty > 0:
            final_inventory_dict[tid] = final_qty

        # 制造产物 = 仅包含有生产量的物品
        if total_produced > 0:
            manufactured_products[tid] = total_produced

    final_product_count = len(manufactured_products)

    print(f"总利润: {total_profit:,.0f}")
    print(f"最终产物种类数: {final_product_count}")

    # ------------------ 输出中文采购清单 CSV ------------------
    with open(PURCHASE_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        total_purchase_cost = 0
        for tid, var in purchase.items():
            qty = int(var.value()) if var.value() else 0
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
        qty = int(var.value()) if var.value() else 0
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
    # 验证利润计算
    print("\n" + "=" * 60)
    print("利润验证")
    print("=" * 60)

    # 方法1：从蓝图评分
    profit_from_blueprints = total_profit

    # 方法2：从最终产物
    final_revenue = sum(final_inventory_dict[tid] * get_price(tid, "buy") for tid in final_products)
    purchase_cost = sum(
        (int(purchase[tid].value()) if purchase[tid].value() else 0) * get_jita_price(tid, "buy")
        for tid in all_items
    )

    # 计算使用的库存价值
    inventory_value = 0
    for tid in all_items:
        inv_qty = inventory.get(tid, 0)
        if inv_qty > 0:
            total_needed = sum(
                (int(x[i].value()) if x[i].value() else 0) *
                next((m["quantity"] for m in get_activity(bp)[0].get("materials", [])
                      if m["typeID"] == tid), 0)
                for i, bp in enumerate(blueprints)
                if get_activity(bp)[0] is not None
            )

            total_produced = sum(
                (int(x[i].value()) if x[i].value() else 0) *
                next((p["quantity"] for p in get_activity(bp)[0].get("products", [])
                      if p["typeID"] == tid), 0)
                for i, bp in enumerate(blueprints)
                if get_activity(bp)[0] is not None
            )

            purchased_qty = int(purchase[tid].value()) if purchase[tid].value() else 0
            used = max(0, total_needed - total_produced - purchased_qty)
            used = min(used, inv_qty)

            if used > 0:
                price = get_jita_price(tid, "buy")
                inventory_value += used * price

    profit_from_final = final_revenue - purchase_cost - inventory_value

    print(f"方法1（蓝图评分）: {profit_from_blueprints:15,.0f}")
    print(f"方法2（产出-成本）: {profit_from_final:15,.0f}")
    print(f"差异:              {abs(profit_from_blueprints - profit_from_final):15,.2f}")

else:
    print("求解失败或无可行解")
