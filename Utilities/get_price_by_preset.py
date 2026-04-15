import argparse
import configparser
import json
import statistics
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


DATASOURCE = "tranquility"
DEFAULT_REGION_IDS = [10000002, 10000003]  # The Forge(Jita), The Vale of the Silent
DEFAULT_REQUEST_INTERVAL = 0.05
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_PRICE_FIELD = "lowest"
DEFAULT_PRESET = "materials_all"
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


def parse_region_ids(raw: Optional[str]) -> list[int]:
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


def _simple_avg(rows: list[dict], key: str) -> float:
    if not rows:
        return 0.0
    return sum(float(r.get(key, 0) or 0) for r in rows) / len(rows)


def get_item_price(
    type_id: int,
    region_id: int,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    use_iqr_filter: bool = True,
    price_field: str = DEFAULT_PRICE_FIELD,
    reference_date: Optional[date] = None,
) -> dict:
    url = f"https://esi.evetech.net/latest/markets/{region_id}/history/?datasource={DATASOURCE}&type_id={type_id}"
    with urlopen(url, timeout=30) as response:
        history = json.loads(response.read().decode("utf-8"))

    if not history:
        return {"average": 0.0, "highest": 0.0, "lowest": 0.0, "order_count": 0.0, "volume": 0.0}

    lookback_days = max(int(lookback_days), 1)
    today = reference_date or date.today()
    cutoff = today - timedelta(days=lookback_days)  # 不含 cutoff 当天，含 today

    # 按日期过滤：仅保留 cutoff < entry_date <= today 的条目
    rows = [
        r for r in history
        if cutoff < date.fromisoformat(r["date"]) <= today
    ]

    if not rows:
        return {"average": 0.0, "highest": 0.0, "lowest": 0.0, "order_count": 0.0, "volume": 0.0}

    filtered_rows = rows
    if use_iqr_filter:
        metric_values = [float(r.get(price_field, 0) or 0) for r in rows]
        bounds = _iqr_bounds(metric_values)
        if bounds is not None:
            low, high = bounds
            candidate = [r for r in rows if low <= float(r.get(price_field, 0) or 0) <= high]
            if candidate:
                filtered_rows = candidate

    # volume 均值：总量 ÷ 总周期天数（无成交日计 0，不除以实际有数据天数）
    total_volume = sum(float(r.get("volume", 0) or 0) for r in filtered_rows)
    avg_volume = total_volume / lookback_days

    return {
        "average": _weighted_avg(filtered_rows, "average"),
        "highest": _weighted_avg(filtered_rows, "highest"),
        "lowest": _weighted_avg(filtered_rows, "lowest"),
        "order_count": _simple_avg(filtered_rows, "order_count"),
        "volume": avg_volume,
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


def _resolve_type_ids(repo_root: Path, alias_file: Path, preset_file: Path, preset_name: str) -> tuple[list[int], list[str], str]:
    aliases = load_json(alias_file).get("aliases", [])
    presets = load_json(preset_file)

    alias_map = {item["alias"]: item["path"] for item in aliases}
    preset = next((item for item in presets if item.get("name") == preset_name), None)
    if preset is None:
        preset = next((item for item in presets if item.get("name") == DEFAULT_PRESET), None)
        if preset is None:
            raise ValueError(f"未找到 preset: {preset_name}，且默认 preset {DEFAULT_PRESET} 也不存在")
        preset_name = DEFAULT_PRESET

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
    return ids, children, preset_name


def main() -> None:
    repo_root = find_repo_root()
    config = configparser.ConfigParser()
    config.read(repo_root / "config.ini", encoding="utf-8")

    default_region_ids = parse_region_ids(config.get("market", "region_ids", fallback="")) or DEFAULT_REGION_IDS
    default_request_interval = config.getfloat("market", "request_interval", fallback=DEFAULT_REQUEST_INTERVAL)

    parser = argparse.ArgumentParser(description="按 preset 获取多区域价格并输出聚合文件")
    parser.add_argument("preset", nargs="?", default=DEFAULT_PRESET, help=f"preset 名称，默认 {DEFAULT_PRESET}")
    parser.add_argument(
        "--region-ids",
        default=",".join(str(x) for x in default_region_ids),
        help="区域ID列表，逗号分隔",
    )
    parser.add_argument("--request-interval", type=float, default=default_request_interval, help="请求间隔秒")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="从后向前取多少天做加权平均")
    parser.add_argument("--price-field", default=DEFAULT_PRICE_FIELD, choices=["average", "highest", "lowest"], help="IQR 异常值过滤基准字段")
    parser.add_argument("--disable-iqr-filter", action="store_true", help="禁用 IQR 异常值过滤")
    parser.add_argument("--force-refresh", action="store_true", help="忽略已有缓存，强制重拉")
    parser.add_argument("--dry-run", action="store_true", help="仅解析 preset，不请求 ESI")
    args = parser.parse_args()

    region_ids = parse_region_ids(args.region_ids)
    if len(region_ids) < 2:
        print("提示：建议至少传入两个区域（例如 Jita 和 Vale）")
    if not region_ids:
        raise ValueError("至少需要一个 region_id")

    alias_file = resolve_path(repo_root, config, "materials_alias_json", "Data/Materials/alias.json")
    preset_file = resolve_path(repo_root, config, "materials_preset_json", "Data/Materials/preset.json")
    cache_dir = resolve_path(repo_root, config, "market_cache_dir", "Cache/Market")

    ids, children, effective_preset = _resolve_type_ids(repo_root, alias_file, preset_file, args.preset)

    if args.dry_run:
        print(f"preset={effective_preset}, region_ids={region_ids}, interval={args.request_interval}")
        print(f"children={children}")
        print(f"resolved_ids={len(ids)}")
        return

    cache_dir.mkdir(parents=True, exist_ok=True)
    output_file = cache_dir / f"price_{effective_preset}.json"
    existing = {} if args.force_refresh else _load_existing_output(output_file)

    for idx, type_id in enumerate(ids, 1):
        entry = existing.get(type_id, {"id": type_id})

        for region_id in region_ids:
            region_name = REGION_NAME_MAP.get(region_id, f"region_{region_id}")
            if region_name in entry and not args.force_refresh:
                continue
            try:
                entry[region_name] = get_item_price(
                    type_id=type_id,
                    region_id=region_id,
                    lookback_days=args.lookback_days,
                    use_iqr_filter=not args.disable_iqr_filter,
                    price_field=args.price_field,
                )
            except (HTTPError, URLError) as exc:
                print(f"请求失败 region={region_id} type_id={type_id}: {exc}")
                entry[region_name] = {"average": 0.0, "highest": 0.0, "lowest": 0.0, "order_count": 0.0, "volume": 0.0}

            time.sleep(args.request_interval)

        existing[type_id] = entry
        _write_output(output_file, existing)
        if idx % 20 == 0 or idx == len(ids):
            print(f"进度 {idx}/{len(ids)}，已写入 {output_file}")

    print(f"完成：{output_file}，共 {len(existing)} 条")


if __name__ == "__main__":
    main()
