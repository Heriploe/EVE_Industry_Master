import json
import csv
from pulp import LpMaximize, LpProblem, LpVariable, lpSum, LpInteger, LpBinary

# ================== 模式 ==================
data_Dir = "Source"
result_Dir = "Results"

# ================== 全局参数 ==================
BUDGET = 200_000_000  # 可调整
ALPHA = 1  # 流动性权重参数
MAX_PROD_FACTOR = 1  # 单产物最大生产量占市场交易量比例
ME = 0.125

FARE_JITA = 500
FARE_VOF = 150

# 新增：最终产物种类惩罚系数（越大越倾向于减少产物种类）
PRODUCT_DIVERSITY_PENALTY = 0 # 可根据实际利润规模调整

# 新增：VOS蓝图过滤开关
FILTER_VOS_BLUEPRINTS = True  # True=启用过滤，False=不过滤

# ================== 文件 ==================
INVENTORY_JSON = f"Inventory/materials.json"
INVENTORY_BLUEPRINTS_JSON = "Inventory/blueprints.json"
BLUEPRINTS_JSON = f"{data_Dir}/blueprints_merged.json"
JITA_PRICES_JSON = f"{data_Dir}/jita_prices.json"
VOS_PRICES_JSON = f"{data_Dir}/vos_prices.json"
TRADE_VOS_JSON = f"{data_Dir}/trade_vos.json"
TYPES_JSON = f"{data_Dir}/types.json"
TYPES_VOLUME_JSON = f"{data_Dir}/types_volume.json"
SHIPS_JSON = f"{data_Dir}/ships.json"
T2_JSON = f"{data_Dir}/T2.json"

PURCHASE_CSV = f"{result_Dir}/purchase_list.csv"
EXECUTION_CSV = f"{result_Dir}/execution_list.csv"
FINAL_PRODUCTS_CSV = f"{result_Dir}/final_products.csv"

# ------------------ 读取数据 ------------------
with open(INVENTORY_JSON, "r", encoding="utf-8") as f:
    raw_inventory = json.load(f)

inventory = {}
if isinstance(raw_inventory, dict):
    # 字典格式也可能有重复，需要累加
    for k, v in raw_inventory.items():
        tid = int(k)
        inventory[tid] = inventory.get(tid, 0) + v
elif isinstance(raw_inventory, list):
    for item in raw_inventory:
        if isinstance(item, dict):
            tid = int(item["type_id"])
            qty = item["quantity"]
            inventory[tid] = inventory.get(tid, 0) + qty
        else:
            tid = int(item[0])
            qty = item[1]
            inventory[tid] = inventory.get(tid, 0) + qty

with open(BLUEPRINTS_JSON, "r", encoding="utf-8") as f:
    blueprints = json.load(f)

with open(JITA_PRICES_JSON, "r", encoding="utf-8") as f:
    jita_prices_raw = json.load(f)
jita_prices = {}
for k, v in jita_prices_raw.items():
    jita_prices[int(k)] = {
        "buy": v["jita"].get("buy") if isinstance(v["jita"].get("buy"), (int, float)) else 0,
        "volume": v["jita"].get("volume", 0) if isinstance(v.get("volume", 0), (int, float)) else 0
    }

with open(VOS_PRICES_JSON, "r", encoding="utf-8") as f:
    vos_prices_raw = json.load(f)
vos_prices = {}
for k, v in vos_prices_raw.items():
    vos_prices[int(k)] = {
        "buy": v["vos"].get("buy") if isinstance(v["vos"].get("buy"), (int, float)) else 0,
        "volume": v["vos"].get("volume", 0) if isinstance(v.get("volume", 0), (int, float)) else 0
    }

# ------------------ 读取 trade_vos.json ------------------
with open(TRADE_VOS_JSON, "r", encoding="utf-8") as f:
    trade_vos_list = json.load(f)

# 创建VOS交易物品ID的集合（用于快速查找）
vos_trade_ids = {int(item["id"]) for item in trade_vos_list}

# ------------------ 读取 types.json ------------------
with open(TYPES_JSON, "r", encoding="utf-8") as f:
    types_list = json.load(f)

# ------------------ 读取 types_volume.json ------------------
with open(TYPES_VOLUME_JSON, "r", encoding="utf-8") as f:
    types_volume_list = json.load(f)

# 创建物品ID到体积的映射
item_volumes = {int(item["id"]): item["volume"] for item in types_volume_list}

# ------------------ 读取 ships.json ------------------
with open(SHIPS_JSON, "r", encoding="utf-8") as f:
    ships_list = json.load(f)

# 创建船只ID的集合（用于体积除以10的判断）
ship_ids = {int(item["id"]) for item in ships_list}

# ------------------ 读取 T2.json ------------------
with open(T2_JSON, "r", encoding="utf-8") as f:
    t2_pairs = json.load(f)

# 创建T1到T2的映射字典
t2_to_t1 = {}
for pair in t2_pairs:
    if len(pair) == 2:
        t1_bp_id = int(pair[0])
        t2_bp_id = int(pair[1])
        t2_to_t1[t1_bp_id] = t2_bp_id

# ------------------ 读取 Inventory/blueprints.json ------------------
with open(INVENTORY_BLUEPRINTS_JSON, "r", encoding="utf-8") as f:
    inventory_blueprints_list = json.load(f)

# 创建蓝图ID到is_blueprint_copy的映射
blueprint_copy_status = {}
for bp in inventory_blueprints_list:
    bp_id = int(bp.get("type_id"))
    is_copy = bp.get("is_blueprint_copy", False)
    blueprint_copy_status[bp_id] = is_copy


# ------------------ 工具函数 ------------------
def get_activity(bp):
    if "manufacturing" in bp:
        return bp["manufacturing"], "manufacturing"
    if "reaction" in bp:
        return bp["reaction"], "reaction"
    return None, None


def get_name(tid):
    return types_map.get(int(tid), {"zh": f"未知_{tid}", "en": f"UNKNOWN_{tid}"})


def get_jita_price(tid, field="buy"):
    item = jita_prices.get(int(tid), {})
    val = item.get(field)
    if isinstance(val, (int, float)):
        return val
    return 0


def get_vos_price(tid, field="buy"):
    item = vos_prices.get(int(tid), {})
    val = item.get(field)
    if isinstance(val, (int, float)):
        return val
    return 0


def get_price(tid, field="buy"):
    """根据物品是否在VOS交易列表中选择合适的价格"""
    if int(tid) in vos_trade_ids:
        return get_vos_price(tid, field)
    else:
        return get_jita_price(tid, field)


def get_volume(tid):
    """获取物品体积，如果是船只则除以10"""
    volume = item_volumes.get(int(tid), 0)
    if int(tid) in ship_ids:
        volume = volume / 10
    return volume


def get_freight_cost(tid, quantity):
    """计算运费成本：单位运费 × 体积 × 数量"""
    volume = get_volume(tid)
    # 根据是否在VOS交易列表选择运费
    if int(tid) in vos_trade_ids:
        unit_fare = FARE_VOF
    else:
        unit_fare = FARE_JITA
    return unit_fare * volume * quantity


def should_filter_blueprint(bp_id, bp):
    """
    判断蓝图是否应该被过滤掉

    规则：
    - 如果FILTER_VOS_BLUEPRINTS=False，不过滤
    - 如果蓝图的产物不在trade_vos中，不过滤
    - 如果蓝图的产物在trade_vos中：
      - 如果is_blueprint_copy=False，保留（不过滤）
      - 如果是对应的T2蓝图，保留（不过滤）
      - 否则过滤掉

    返回True表示应该过滤掉，False表示保留
    """
    if not FILTER_VOS_BLUEPRINTS:
        return False  # 不启用过滤

    # 获取产物
    activity, _ = get_activity(bp)
    products = activity.get("products", [])
    # 检查所有产物是否在VOS交易列表中
    all_products_in_vos = all(p["typeID"] in vos_trade_ids for p in products)

    # 1. 检查是否是原本（非复制品）
    is_copy = blueprint_copy_status.get(bp_id, True)  # 默认当作copy
    if all_products_in_vos and not is_copy:
        return False  # 是原本，保留
    bp_id_t1=bp_id
    if bp_id in t2_to_t1:
        bp_id_t1 = t2_to_t1[bp_id]
    is_t2_copy = blueprint_copy_status.get(bp_id_t1, True)
    if all_products_in_vos and not is_t2_copy:
        return False

    # 是VOS产物的蓝图复制品，且不是T2蓝图，过滤掉
    return True


# 建立 id → 名称映射
types_map = {}
for item in types_list:
    tid = item.get("id")
    if tid is not None:
        types_map[int(tid)] = {
            "zh": item.get("zh", f"未知_{tid}"),
            "en": item.get("en", f"UNKNOWN_{tid}")
        }

# 建立蓝图ID → 名称映射
bp_names = {}
for bp in blueprints:
    bp_id = bp.get("blueprintTypeID")
    if bp_id:
        bp_names[bp_id] = get_name(bp_id) if bp_id in types_map else {"zh": f"蓝图_{bp_id}", "en": f"BP_{bp_id}"}

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
    vol = vos_prices.get(tid, {}).get("volume", 0) if tid in vos_trade_ids else jita_prices.get(tid, {}).get("volume",
                                                                                                             0)
    total_market_value += sell * vol

# ------------------ 计算蓝图评分 ------------------
bp_score = {}

for i, bp in enumerate(blueprints):
    # 获取蓝图ID
    bp_id = bp.get("blueprintTypeID")

    # 应用过滤
    if should_filter_blueprint(bp_id, bp):
        bp_score[i] = 0
        continue

    activity, act_type = get_activity(bp)
    if not activity:
        bp_score[i] = 0
        continue

    # 材料成本（统一从Jita购买）
    materials_cost = sum(
        m.get("quantity", 0) * get_jita_price(m["typeID"], "buy")
        for m in activity.get("materials", [])
    )
    
    # 分别计算Jita和VOS的利润
    # Jita方案
    jita_products_value = sum(
        p.get("quantity", 0) * get_jita_price(p["typeID"], "buy")
        for p in activity.get("products", [])
    )
    jita_freight_cost = sum(
        FARE_JITA * get_volume(p["typeID"]) * p.get("quantity", 0)
        for p in activity.get("products", [])
    )
    
    # VOS方案
    vos_products_value = sum(
        p.get("quantity", 0) * get_vos_price(p["typeID"], "buy")
        for p in activity.get("products", [])
    )
    vos_freight_cost = sum(
        FARE_VOF * get_volume(p["typeID"]) * p.get("quantity", 0)
        for p in activity.get("products", [])
    )
    
    # 计算两种方案的利润
    if act_type == "manufacturing":
        jita_profit = jita_products_value - materials_cost * (1 - ME) - jita_freight_cost
        vos_profit = vos_products_value - materials_cost * (1 - ME) - vos_freight_cost
    else:
        jita_profit = jita_products_value - materials_cost - jita_freight_cost
        vos_profit = vos_products_value - materials_cost - vos_freight_cost
    
    # 取最大利润
    profit = max(jita_profit, vos_profit)
    
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
                writer.writerow([get_name(tid)["zh"], qty])

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
                bp_name = get_name(bp_id)["zh"] if bp_id in types_map else f"蓝图_{bp_id}"

                # 获取产物名称
                products = activity.get("products", [])
                product_names = ", ".join([get_name(p["typeID"])["zh"] for p in products])

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
            writer.writerow([get_name(tid)["zh"], qty])

    print(f"完成：采购清单 → {PURCHASE_CSV}")
    print(f"完成：执行清单 → {EXECUTION_CSV}")
    print(f"完成：最终产物总量 → {FINAL_PRODUCTS_CSV}")

    # ------------------ 合并材料库存和采购清单为 initial_inventory.json ------------------
    INITIAL_INVENTORY_JSON = "Inventory/initial_inventory.json"

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
                "zh": get_name(tid)["zh"],
                "en": get_name(tid)["en"],
                "quantity": qty
            })

    # 写入JSON文件
    with open(INITIAL_INVENTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(initial_inventory_list, f, ensure_ascii=False, indent=2)

    print(f"完成：初始库存 → {INITIAL_INVENTORY_JSON}")

    # ------------------ 输出最终库存为 final_inventory.json ------------------
    FINAL_INVENTORY_JSON = "Inventory/final_inventory.json"

    # 转换为列表格式（与materials.json格式一致）
    final_inventory_list = []
    for tid, qty in sorted(final_inventory_dict.items()):
        if qty > 0:
            final_inventory_list.append({
                "type_id": tid,
                "zh": get_name(tid)["zh"],
                "en": get_name(tid)["en"],
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
