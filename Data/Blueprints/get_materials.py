import os
import json

# 文件夹路径
source_folder = "Test"  # 蓝图 JSON 文件夹
types_file = "types.json"
output_folder = "Test"    # 输出文件夹

os.makedirs(output_folder, exist_ok=True)

# 读取 types.json
with open(types_file, "r", encoding="utf-8") as f:
    types_list = json.load(f)
id_to_name = {item["id"]: {"zh": item["zh"], "en": item["en"]} for item in types_list}

# 遍历蓝图 JSON 文件
for filename in os.listdir(source_folder):
    if not filename.endswith(".json"):
        continue

    json_path = os.path.join(source_folder, filename)
    with open(json_path, "r", encoding="utf-8") as f:
        blueprints = json.load(f)

    product_list = []

    for bp in blueprints:
        # 处理 manufacturing.products
        manufacturing = bp.get("manufacturing", {})
        for p in manufacturing.get("products", []):
            typeID = p.get("typeID")
            if typeID:
                product_list.append({
                    "id": typeID,
                    "zh": id_to_name.get(typeID, {}).get("zh", ""),
                    "en": id_to_name.get(typeID, {}).get("en", "")
                })

        # 处理 reaction.products
        reaction = bp.get("reaction", {})
        for p in reaction.get("products", []):
            typeID = p.get("typeID")
            if typeID:
                product_list.append({
                    "id": typeID,
                    "zh": id_to_name.get(typeID, {}).get("zh", ""),
                    "en": id_to_name.get(typeID, {}).get("en", "")
                })

    # 去重 products
    seen_ids = set()
    unique_products = []
    for prod in product_list:
        if prod["id"] not in seen_ids:
            unique_products.append(prod)
            seen_ids.add(prod["id"])

    # 输出文件
    output_file = os.path.join(output_folder, filename.replace("_blueprints.json", ".json"))
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(unique_products, f, ensure_ascii=False, indent=2)

    print(f"{filename} -> {output_file} 导出 {len(unique_products)} 条产品")
