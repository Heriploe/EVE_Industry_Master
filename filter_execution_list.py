import argparse
import configparser
import csv
import json
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_JITA_PRICES = "Cache/Input/jita_prices.json"
DEFAULT_FINAL_PRODUCTS = "Cache/Output/final_products.csv"
DEFAULT_EXECUTION_LIST = "Cache/Output/execution_list.csv"
DEFAULT_OUTPUT = "Cache/Output/final_products_filtered.csv"
DEFAULT_MIN_VALUE = 25_000_000


def resolve_path(value: str) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p


def load_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(REPO_ROOT / "config.ini", encoding="utf-8")
    return config


def parse_line(line: str) -> Tuple[str, float]:
    raw = line.strip()
    if not raw:
        return "", 0.0

    if "\t" in raw:
        name, qty = raw.rsplit("\t", 1)
    elif "," in raw:
        name, qty = raw.rsplit(",", 1)
    else:
        return raw, 0.0

    name = name.strip()
    try:
        quantity = float(qty.strip())
    except ValueError:
        quantity = 0.0
    return name, quantity


def load_simple_rows(path: Path) -> List[str]:
    with path.open("r", encoding="utf-8-sig") as f:
        return [line.rstrip("\n") for line in f if line.strip()]


def load_prices(path: Path):
    data = json.load(path.open("r", encoding="utf-8"))
    by_name = {}
    for row in data.values():
        zh = (row or {}).get("zh")
        en = (row or {}).get("en")
        buy = ((row or {}).get("jita") or {}).get("buy", 0)
        try:
            price = float(buy or 0)
        except (TypeError, ValueError):
            price = 0.0
        if zh:
            by_name[zh] = price
        if en:
            by_name[en] = price
    return by_name


def main():
    config = load_config()
    default_jita = config.get("calculator", "jita_prices_json", fallback=DEFAULT_JITA_PRICES)
    default_final = str((resolve_path(config.get("calculator", "output_dir", fallback="Cache/Output")) / "final_products.csv").relative_to(REPO_ROOT))
    default_exec = str((resolve_path(config.get("calculator", "output_dir", fallback="Cache/Output")) / "execution_list.csv").relative_to(REPO_ROOT))

    parser = argparse.ArgumentParser(description="按 final_products 总价值过滤 execution_list")
    parser.add_argument("--jita-prices", default=default_jita, help="jita_prices.json 路径")
    parser.add_argument("--final-products", default=default_final, help="final_products.csv 路径")
    parser.add_argument("--execution-list", default=default_exec, help="execution_list.csv 路径")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="过滤后输出路径（默认 final_products_filtered.csv）")
    parser.add_argument("--min-total-value", type=float, default=DEFAULT_MIN_VALUE, help="最小总价值阈值，默认 25000000")
    args = parser.parse_args()

    jita_path = resolve_path(args.jita_prices)
    final_path = resolve_path(args.final_products)
    exec_path = resolve_path(args.execution_list)
    output_path = resolve_path(args.output)

    if not jita_path.exists():
        raise FileNotFoundError(f"未找到 jita_prices.json: {jita_path}")
    if not final_path.exists():
        raise FileNotFoundError(f"未找到 final_products.csv: {final_path}")
    if not exec_path.exists():
        raise FileNotFoundError(f"未找到 execution_list.csv: {exec_path}")

    prices = load_prices(jita_path)
    final_rows = load_simple_rows(final_path)
    execution_rows = load_simple_rows(exec_path)

    if len(final_rows) != len(execution_rows):
        raise ValueError(
            f"行数不一致: final_products={len(final_rows)} execution_list={len(execution_rows)}，无法按行对应过滤"
        )

    kept = []
    dropped = 0
    for final_line, exec_line in zip(final_rows, execution_rows):
        product_name, quantity = parse_line(final_line)
        unit_price = float(prices.get(product_name, 0.0))
        total_value = quantity * unit_price

        if total_value >= float(args.min_total_value):
            kept.append(exec_line)
        else:
            dropped += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        for line in kept:
            if "\t" in line:
                left, right = line.rsplit("\t", 1)
            elif "," in line:
                left, right = line.rsplit(",", 1)
            else:
                left, right = line, ""
            writer.writerow([left.strip(), right.strip()])

    print(
        json.dumps(
            {
                "final_products": len(final_rows),
                "execution_list": len(execution_rows),
                "kept": len(kept),
                "dropped": dropped,
                "min_total_value": float(args.min_total_value),
                "output": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
