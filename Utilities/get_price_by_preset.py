import argparse
import configparser
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


DATASOURCE = "tranquility"
DEFAULT_REGION_ID = 10000002
DEFAULT_REQUEST_INTERVAL = 0.05


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


def get_jita_buy_price(type_id: int, region_id: int) -> float:
    url = f"https://esi.evetech.net/latest/markets/{region_id}/history/?datasource={DATASOURCE}&type_id={type_id}"
    with urlopen(url, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not data:
        return 0
    return data[-1].get("lowest", 0) or 0


def main() -> None:
    repo_root = find_repo_root()
    config = configparser.ConfigParser()
    config.read(repo_root / "config.ini", encoding="utf-8")

    default_region_id = config.getint("market", "region_id", fallback=DEFAULT_REGION_ID)
    default_request_interval = config.getfloat("market", "request_interval", fallback=DEFAULT_REQUEST_INTERVAL)

    parser = argparse.ArgumentParser(description="按 preset 获取市场价格并缓存到 Cache/Market")
    parser.add_argument("preset", help="preset entry 名称")
    parser.add_argument("--region-id", type=int, default=default_region_id, help="市场区域ID")
    parser.add_argument("--request-interval", type=float, default=default_request_interval, help="请求间隔秒")
    parser.add_argument("--dry-run", action="store_true", help="仅解析 preset，不请求 ESI")
    args = parser.parse_args()

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
        print(f"preset={args.preset}, region_id={args.region_id}, interval={args.request_interval}")
        print(f"children={children}")
        print(f"resolved_ids={len(ids)}")
        return

    result = []
    for type_id in ids:
        try:
            buy = get_jita_buy_price(type_id, args.region_id)
        except (HTTPError, URLError) as exc:
            print(f"请求失败 type_id={type_id}: {exc}")
            buy = 0

        result.append({"id": type_id, "buy": buy})
        time.sleep(args.request_interval)

    cache_dir.mkdir(parents=True, exist_ok=True)
    output_file = cache_dir / f"{args.preset}_region_{args.region_id}.json"

    with output_file.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"已保存 {len(result)} 条价格到: {output_file}")


if __name__ == "__main__":
    main()
