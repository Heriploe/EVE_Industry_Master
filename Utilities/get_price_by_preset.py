import argparse
import configparser
import json
import statistics
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


DATASOURCE = "tranquility"
DEFAULT_REGION_IDS = [10000002, 10000003]  # The Forge(Jita), The Vale of the Silent
DEFAULT_REQUEST_INTERVAL = 0.05
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_PRICE_FIELD = "lowest"
REGION_NAME_MAP = {
    10000002: "jita",
    10000003: "vale_of_the_silent",
}


def find_repo_root() -> Path:
    current_dir = Path(__file__).resolve().parent
    return next((p for p in [current_dir, *current_dir.parents] if (p / "config.ini").exists()), current_dir)


def resolve_path(repo_root: Path, config: configparser.ConfigParser, key: str, fallback: str) -> Path:
    value = config.get("paths", key, fallback=fallback)
    path = Path(value)
    if not path.is_absolute():
        path = repo_root / path
    return path


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_region_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    result = []
    seen = set()
    for part in raw.replace(";", ",").split(","):
        token = part.strip()
        if not token:
            continue
        rid = int(token)
        if rid not in seen:
            seen.add(rid)
            result.append(rid)
    return result


def _iqr_bounds(values: list[float]) -> tuple[float, float] | None:
    if len(values) < 4:
        return None
    sorted_vals = sorted(values)
    q1 = statistics.quantiles(sorted_vals, n=4, method="inclusive")[0]
    q3 = statistics.quantiles(sorted_vals, n=4, method="inclusive")[2]
    iqr = q3 - q1
    if iqr <= 0:
        return None
    return q1 - 1.5 * iqr, q3 + 1.5 * iqr


def _weighted_avg(rows: list[dict], value_key: str, weight_key: str = "volume") -> float:
    total_weight = 0.0
    total_value = 0.0
    for row in rows:
        val = float(row.get(value_key, 0) or 0)
        w = float(row.get(weight_key, 0) or 0)
        if w < 0:
            continue
        total_weight += w
        total_value += val * w
    if total_weight <= 0:
        if not rows:
            return 0.0
        return sum(float(r.get(value_key, 0) or 0) for r in rows) / len(rows)
    return total_value / total_weight


def get_item_price(
    type_id: int,
    region_id: int,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    use_iqr_filter: bool = True,
    price_field: str = DEFAULT_PRICE_FIELD,
) -> dict:
    url = f"https://esi.evetech.net/latest/markets/{region_id}/history/?datasource={DATASOURCE}&type_id={type_id}"
    with urlopen(url, timeout=30) as response:
        history = json.loads(response.read().decode("utf-8"))

    if not history:
        return {
            "id": int(type_id),
            "region_id": int(region_id),
            "buy": 0,
            "average": 0,
            "highest": 0,
            "lowest": 0,
            "order_count": 0,
            "volume": 0,
            "weighted": {
                "days_used": 0,
                "days_total": 0,
                "filtered_outliers": 0,
                "price_field": price_field,
                "average": 0,
                "highest": 0,
                "lowest": 0,
                "order_count": 0,
                "volume": 0,
            },
            "history": [],
        }

    lookback_days = max(int(lookback_days), 1)
    rows = history[-lookback_days:]

    filtered_rows = rows
    filtered_outliers = 0
    if use_iqr_filter and rows:
        metric_values = [float(r.get(price_field, 0) or 0) for r in rows]
        bounds = _iqr_bounds(metric_values)
        if bounds is not None:
            low, high = bounds
            filtered_rows = [r for r in rows if low <= float(r.get(price_field, 0) or 0) <= high]
            filtered_outliers = len(rows) - len(filtered_rows)
            if not filtered_rows:
                filtered_rows = rows

    latest = history[-1]
    weighted_avg = _weighted_avg(filtered_rows, "average")
    weighted_high = _weighted_avg(filtered_rows, "highest")
    weighted_low = _weighted_avg(filtered_rows, "lowest")
    weighted_orders = _weighted_avg(filtered_rows, "order_count")
    weighted_volume = _weighted_avg(filtered_rows, "volume")

    return {
        "id": int(type_id),
        "region_id": int(region_id),
        # 向后兼容：restore_ore 等仍可读取 buy
        "buy": weighted_low,
        "average": float(latest.get("average", 0) or 0),
        "highest": float(latest.get("highest", 0) or 0),
        "lowest": float(latest.get("lowest", 0) or 0),
        "order_count": float(latest.get("order_count", 0) or 0),
        "volume": float(latest.get("volume", 0) or 0),
        "date": latest.get("date", ""),
        "weighted": {
            "days_used": len(filtered_rows),
            "days_total": len(rows),
            "filtered_outliers": filtered_outliers,
            "price_field": price_field,
            "average": weighted_avg,
            "highest": weighted_high,
            "lowest": weighted_low,
            "order_count": weighted_orders,
            "volume": weighted_volume,
        },
        # 保留原始数据字段，便于后续二次处理
        "history": [
            {
                "date": r.get("date", ""),
                "average": float(r.get("average", 0) or 0),
                "highest": float(r.get("highest", 0) or 0),
                "lowest": float(r.get("lowest", 0) or 0),
                "order_count": float(r.get("order_count", 0) or 0),
                "volume": float(r.get("volume", 0) or 0),
            }
            for r in rows
        ],
    }


def _load_existing_output(output_file: Path) -> dict[int, dict]:
    if not output_file.exists():
        return {}
    try:
        with output_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return {}

    result = {}
    if isinstance(data, list):
        for item in data:
            try:
                result[int(item["id"])] = item
            except Exception:
                continue
    return result


def _write_output(output_file: Path, entries_map: dict[int, dict]) -> None:
    payload = [entries_map[k] for k in sorted(entries_map.keys())]
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    repo_root = find_repo_root()
    config = configparser.ConfigParser()
    config.read(repo_root / "config.ini", encoding="utf-8")

    default_region_ids = parse_region_ids(config.get("market", "region_ids", fallback="")) or DEFAULT_REGION_IDS
    default_request_interval = config.getfloat("market", "request_interval", fallback=DEFAULT_REQUEST_INTERVAL)

    parser = argparse.ArgumentParser(description="按 preset 获取多个区域市场价格并缓存到 Cache/Market")
    parser.add_argument("preset", help="preset entry 名称")
    parser.add_argument(
        "--region-ids",
        default=",".join(str(x) for x in default_region_ids),
        help="区域ID列表，逗号分隔（默认包含 Jita 与 Vale of the Silent）",
    )
    parser.add_argument("--request-interval", type=float, default=default_request_interval, help="请求间隔秒")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="从后向前取多少天做加权平均")
    parser.add_argument("--price-field", default=DEFAULT_PRICE_FIELD, choices=["average", "highest", "lowest"], help="IQR 异常值过滤基准字段")
    parser.add_argument("--disable-iqr-filter", action="store_true", help="禁用 IQR 异常值过滤")
    parser.add_argument("--force-refresh", action="store_true", help="忽略已有缓存，强制重拉")
    parser.add_argument("--dry-run", action="store_true", help="仅解析 preset，不请求 ESI")
    args = parser.parse_args()

    region_ids = parse_region_ids(args.region_ids)
    if not region_ids:
        raise ValueError("至少需要一个 region_id")

    alias_file = resolve_path(repo_root, config, "materials_alias_json", "Data/Materials/alias.json")
    preset_file = resolve_path(repo_root, config, "materials_preset_json", "Data/Materials/preset.json")
    cache_dir = resolve_path(repo_root, config, "market_cache_dir", "Cache/Market")

    aliases = load_json(alias_file).get("aliases", [])
    presets = load_json(preset_file)

    alias_map = {item["alias"]: item["path"] for item in aliases}
    preset = next((item for item in presets if item.get("name") == args.preset), None)
    if preset is None:
        raise ValueError(f"未找到 preset: {args.preset}")

    children = preset.get("children", [])
    ids = []
    seen = set()

    for child_alias in children:
        json_rel_path = alias_map.get(child_alias)
        if not json_rel_path:
            raise ValueError(f"alias 不存在: {child_alias}")
        child_data = load_json(repo_root / json_rel_path)
        for item in child_data:
            type_id = int(item["id"])
            if type_id not in seen:
                seen.add(type_id)
                ids.append(type_id)

    if args.dry_run:
        print(f"preset={args.preset}, region_ids={region_ids}, interval={args.request_interval}")
        print(f"children={children}")
        print(f"resolved_ids={len(ids)}")
        return

    cache_dir.mkdir(parents=True, exist_ok=True)

    for region_id in region_ids:
        region_name = REGION_NAME_MAP.get(region_id, f"region_{region_id}")
        output_file = cache_dir / f"{args.preset}_{region_name}_{region_id}.json"
        existing = {} if args.force_refresh else _load_existing_output(output_file)

        print(f"开始区域 {region_id} ({region_name})：已缓存 {len(existing)} 条")
        for idx, type_id in enumerate(ids, 1):
            if type_id in existing and not args.force_refresh:
                continue

            try:
                item_price = get_item_price(
                    type_id=type_id,
                    region_id=region_id,
                    lookback_days=args.lookback_days,
                    use_iqr_filter=not args.disable_iqr_filter,
                    price_field=args.price_field,
                )
            except (HTTPError, URLError) as exc:
                print(f"请求失败 region={region_id} type_id={type_id}: {exc}")
                item_price = {
                    "id": type_id,
                    "region_id": region_id,
                    "buy": 0,
                    "average": 0,
                    "highest": 0,
                    "lowest": 0,
                    "order_count": 0,
                    "volume": 0,
                    "error": str(exc),
                    "weighted": {
                        "days_used": 0,
                        "days_total": 0,
                        "filtered_outliers": 0,
                        "price_field": args.price_field,
                        "average": 0,
                        "highest": 0,
                        "lowest": 0,
                        "order_count": 0,
                        "volume": 0,
                    },
                    "history": [],
                }

            existing[type_id] = item_price
            _write_output(output_file, existing)

            if idx % 20 == 0 or idx == len(ids):
                print(f"[{region_name}] 进度 {idx}/{len(ids)}，已写入 {output_file}")

            time.sleep(args.request_interval)

        print(f"区域完成 {region_id} ({region_name}) -> {output_file}，共 {len(existing)} 条")


if __name__ == "__main__":
    main()
