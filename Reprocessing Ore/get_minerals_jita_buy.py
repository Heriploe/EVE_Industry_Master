import json
import requests
import time

REGION_ID = 10000002  # 吉他
REQUEST_INTERVAL = 0.05  # 防限流
DATASOURCE = "tranquility"


def get_jita_buy_price(type_id, region_id=REGION_ID):
    url = f"https://esi.evetech.net/latest/markets/{region_id}/history/?datasource={DATASOURCE}&type_id={type_id}"
    r = requests.get(url)
    r.raise_for_status()
    data = r.json()
    print(data[-1])
    if not data:
        return 0
    return data[-1]['lowest']



def main():
    with open("minerals.json", "r", encoding="utf-8") as f:
        minerals = json.load(f)
    result = []

    for m in minerals:
        type_id = m["id"]

        price = get_jita_buy_price(type_id)
        if price is None:
            price = 0

        result.append({
            "id": type_id,
            "buy": price
        })

        # ESI 防速率限制
        time.sleep(0.3)

    with open("jita_prices.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("已导出 jita_prices.json")


if __name__ == "__main__":
    main()
