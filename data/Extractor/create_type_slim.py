import yaml
import json

# 读取 types.yaml 文件
with open("types.yaml", "r", encoding="utf-8") as f:
    data = yaml.safe_load(f)

# 输出列表
result = []
for type_id, entry in data.items():
    item = {
        "id": type_id,
        "groupID": entry.get("groupID", None),
        "metaGroupID": entry.get("metaGroupID", None),
        "marketGroupID": entry.get("marketGroupID", None),
        "basePrice": entry.get("basePrice", None),
        "volume": entry.get("volume", None),
        "zh": entry.get("name", {}).get("zh", ""),
        "en": entry.get("name", {}).get("en", ""),
        "published": entry.get("published", None),
    }
    result.append(item)

# 保存为 JSON 文件
with open("types.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print("导出完成，共导出", len(result), "条记录")
