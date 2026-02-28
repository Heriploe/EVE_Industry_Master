import json

# ================= 配置文件 =================
TYPES_FILE = "types.json"
ASSETS_FILE = "corp_assets.json"
BLUEPRINTS_FILE = "corp_blueprints.json"

OUTPUT_NON_BP = "final_non_blueprints.json"
OUTPUT_BP = "final_blueprints.json"
# ==========================================

def load_types(types_file):
    """
    构建 type_id -> {zh, en} 映射
    """
    with open(types_file, "r", encoding="utf-8") as f:
        types = json.load(f)
    type_dict = {t["id"]: {"zh": t.get("zh", ""), "en": t.get("en", "")} for t in types}
    return type_dict


def main():
    # 1️⃣ 读取类型映射
    type_dict = load_types(TYPES_FILE)

    # 2️⃣ 读取资产和蓝图
    with open(ASSETS_FILE, "r", encoding="utf-8") as f:
        assets = json.load(f)

    with open(BLUEPRINTS_FILE, "r", encoding="utf-8") as f:
        blueprints = json.load(f)

    # 3️⃣ 构建蓝图 item_id 查找表
    blueprint_lookup = {bp["item_id"]: bp for bp in blueprints}

    non_blueprint_assets = []
    blueprint_assets = []

    # 4️⃣ 遍历资产
    for asset in assets:
        item_id = asset["item_id"]
        type_id = asset["type_id"]
        quantity = asset.get("quantity", 1)
        is_blueprint_copy = asset.get("is_blueprint_copy", False)
        names = type_dict.get(type_id, {"zh": "", "en": ""})

        # 检查是否在蓝图列表里
        if item_id in blueprint_lookup:
            bp = blueprint_lookup[item_id]
            blueprint_assets.append({
                "id": type_id,
                "zh": names["zh"],
                "en": names["en"],
                "material_efficiency": bp.get("material_efficiency", 0),
                "time_efficiency": bp.get("time_efficiency", 0),
                "runs": bp.get("runs", -1),
                "is_blueprint_copy": is_blueprint_copy
            })
        else:
            non_blueprint_assets.append({
                "id": type_id,
                "zh": names["zh"],
                "en": names["en"],
                "quantity": quantity
            })

    # 5️⃣ 输出 JSON 文件
    with open(OUTPUT_NON_BP, "w", encoding="utf-8") as f:
        json.dump(non_blueprint_assets, f, ensure_ascii=False, indent=2)

    with open(OUTPUT_BP, "w", encoding="utf-8") as f:
        json.dump(blueprint_assets, f, ensure_ascii=False, indent=2)

    print(f"非蓝图资产 {len(non_blueprint_assets)} 条 -> {OUTPUT_NON_BP}")
    print(f"蓝图资产 {len(blueprint_assets)} 条 -> {OUTPUT_BP}")


if __name__ == "__main__":
    main()
