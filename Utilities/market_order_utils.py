"""
market_order_utils.py — 实时订单查询工具

从 ESI /markets/{region}/orders/ 端点查询当前挂单（买单/卖单），
用于需要实时买卖价格的场景（与 get_price_all.py 基于历史成交记录不同）。

提供的函数
----------
fetch_best_buy(type_id, region_id, station_id, timeout)
    → 指定空间站 buy 单最高价

fetch_min_sell(type_id, region_id, station_id, order_type, timeout)
    → 指定空间站 sell 单最低价

fetch_sell_and_buy(type_id, region_id, station_id, timeout)
    → 同时返回 (min_sell, max_buy)

fetch_region_prices(type_ids, region_id, station_id, order_type, timeout)
    → 批量查询，返回 {str(type_id): min_sell_price}

fetch_structure_prices(structure_id, token, timeout)
    → 建筑全量订单，返回 {str(type_id): min_sell_price}
    以 cache_file 的 mtime 判断是否过期（TTL 由调用方传入）

get_structure_token(config, repo_root)
    → 从 esi_auth 段获取有效 Bearer token

缓存策略
--------
- region 价格：始终实时查询，无缓存。
- structure 价格：以缓存文件 mtime 为时间戳，TTL 由调用方指定（小时）。
  缓存文件路径在调用方指定；缓存未过期时跳过全量拉取。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ESI_BASE = "https://esi.evetech.net/latest"


# ─────────────────────────────────────────────────────────────
# Low-level ESI helper
# ─────────────────────────────────────────────────────────────

def _esi_get(url: str, timeout: float,
             token: Optional[str] = None) -> Optional[list]:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  [ESI] HTTP {e.code}: {body[:200]}")
        return None
    except Exception as e:
        print(f"  [ESI] 请求失败: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# 单 type 实时查询
# ─────────────────────────────────────────────────────────────

def fetch_best_buy(
    type_id: int,
    region_id: int,
    station_id: int,
    timeout: float = 10.0,
) -> Optional[float]:
    """返回指定空间站 buy 最高价，无数据返回 None。"""
    buy_prices: List[float] = []
    page = 1
    while True:
        url = (f"{_ESI_BASE}/markets/{region_id}/orders/"
               f"?datasource=tranquility&order_type=buy"
               f"&type_id={type_id}&page={page}")
        orders = _esi_get(url, timeout)
        if not orders:
            break
        for o in orders:
            if not o.get("is_buy_order", False):
                continue
            if station_id and o.get("location_id") != station_id:
                continue
            p = o.get("price")
            if p is not None:
                buy_prices.append(float(p))
        if len(orders) < 1000:
            break
        page += 1
    return max(buy_prices) if buy_prices else None


def fetch_min_sell(
    type_id: int,
    region_id: int,
    station_id: int,
    order_type: str = "sell",
    timeout: float = 10.0,
) -> Optional[float]:
    """返回指定空间站 sell 最低价，无数据返回 None。"""
    sell_prices: List[float] = []
    page = 1
    while True:
        url = (f"{_ESI_BASE}/markets/{region_id}/orders/"
               f"?datasource=tranquility&order_type={order_type}"
               f"&type_id={type_id}&page={page}")
        orders = _esi_get(url, timeout)
        if not orders:
            break
        for o in orders:
            if order_type == "sell" and o.get("is_buy_order", False):
                continue
            if order_type == "buy" and not o.get("is_buy_order", True):
                continue
            if station_id and o.get("location_id") != station_id:
                continue
            p = o.get("price")
            if p is not None:
                sell_prices.append(float(p))
        if len(orders) < 1000:
            break
        page += 1
    return min(sell_prices) if sell_prices else None


def fetch_sell_and_buy(
    type_id: int,
    region_id: int,
    station_id: int,
    timeout: float = 10.0,
) -> Tuple[Optional[float], Optional[float]]:
    """
    一次请求同时取 sell 最低价和 buy 最高价。
    返回 (min_sell, max_buy)。
    """
    sell_prices: List[float] = []
    buy_prices:  List[float] = []
    page = 1
    while True:
        url = (f"{_ESI_BASE}/markets/{region_id}/orders/"
               f"?datasource=tranquility&order_type=all"
               f"&type_id={type_id}&page={page}")
        orders = _esi_get(url, timeout)
        if not orders:
            break
        for o in orders:
            if station_id and o.get("location_id") != station_id:
                continue
            p = o.get("price")
            if p is None:
                continue
            if o.get("is_buy_order", False):
                buy_prices.append(float(p))
            else:
                sell_prices.append(float(p))
        if len(orders) < 1000:
            break
        page += 1
    return (
        min(sell_prices) if sell_prices else None,
        max(buy_prices)  if buy_prices  else None,
    )


# ─────────────────────────────────────────────────────────────
# 批量区域价格（始终实时，无缓存）
# ─────────────────────────────────────────────────────────────

def fetch_region_prices(
    type_ids: List[int],
    region_id: int,
    station_id: int,
    order_type: str = "sell",
    timeout: float = 10.0,
) -> Dict[str, float]:
    """
    批量查询区域市场最低卖单价（或最高买单价）。
    始终实时查询，不使用缓存。
    返回 {str(type_id): price}。
    """
    print(f"[market_order_utils] 拉取 region={region_id} 价格（{len(type_ids)} 个物品）...")
    results: Dict[str, float] = {}
    for tid in type_ids:
        price = fetch_min_sell(tid, region_id, station_id, order_type, timeout)
        if price is not None:
            results[str(tid)] = price
    return results


# ─────────────────────────────────────────────────────────────
# 建筑市场价格（带文件 mtime 缓存）
# ─────────────────────────────────────────────────────────────

def _cache_valid(cache_file: str, ttl_hours: float) -> bool:
    if ttl_hours <= 0 or not cache_file:
        return False
    try:
        age_h = (time.time() - os.path.getmtime(cache_file)) / 3600
        return age_h <= ttl_hours
    except OSError:
        return False


def _cache_age_str(cache_file: str) -> str:
    try:
        age_h = (time.time() - os.path.getmtime(cache_file)) / 3600
        return f"{age_h:.1f}h 前"
    except OSError:
        return "未知"


def _load_json(path: str) -> Optional[Dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[market_order_utils] 读取 {path} 失败: {e}")
        return None


def _save_json(path: str, data: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[market_order_utils] 写入 {path} 失败: {e}")


def fetch_structure_prices(
    structure_id: int,
    token: str,
    timeout: float = 10.0,
) -> Dict[str, float]:
    """
    拉取建筑全量订单并提取每个 type 的最低卖单价。
    返回 {str(type_id): min_sell_price}。
    """
    all_orders: List[dict] = []
    page = 1
    base = f"{_ESI_BASE}/markets/structures/{structure_id}/"
    print(f"[market_order_utils] 拉取建筑市场全量订单（id={structure_id}）...")

    while True:
        url = base + "?" + urllib.parse.urlencode({"page": page})
        data = _esi_get(url, timeout, token=token)
        if not data:
            break
        all_orders.extend(data)
        print(f"  页 {page}: {len(data)} 条，累计 {len(all_orders)}")
        if len(data) < 1000:
            break
        page += 1

    print(f"  ✓ 共 {len(all_orders)} 条订单")

    sell_map: Dict[str, List[float]] = {}
    for o in all_orders:
        if o.get("is_buy_order", True):
            continue
        tid = str(o.get("type_id", ""))
        p   = o.get("price")
        if tid and p is not None:
            sell_map.setdefault(tid, []).append(float(p))

    return {tid: min(prices) for tid, prices in sell_map.items()}


def get_structure_prices_cached(
    structure_id: int,
    token: Optional[str],
    cache_file: str,
    ttl_hours: float = 24.0,
    timeout: float = 10.0,
) -> Dict[str, float]:
    """
    带文件 mtime 缓存的建筑市场价格查询。
    缓存未过期 → 直接读文件，无 ESI 请求（token 可为 None）。
    缓存已过期 → 拉取全量订单，覆盖写缓存文件。
    返回 {str(type_id): min_sell_price}。
    """
    if cache_file and _cache_valid(cache_file, ttl_hours):
        age = _cache_age_str(cache_file)
        print(f"[market_order_utils] 建筑价格缓存命中（{age}），跳过 ESI 请求")
        data = _load_json(cache_file) or {}
        return {str(k): float(v) for k, v in data.items()}

    if not token:
        print("[market_order_utils] ✗ 建筑缓存已过期但无 token，跳过建筑查询")
        if cache_file and os.path.isfile(cache_file):
            print("[market_order_utils]   使用过期缓存数据")
            data = _load_json(cache_file) or {}
            return {str(k): float(v) for k, v in data.items()}
        return {}

    fresh = fetch_structure_prices(structure_id, token, timeout)
    if cache_file and fresh:
        _save_json(cache_file, fresh)
        print(f"[market_order_utils] 建筑价格缓存已保存: {cache_file}")
    return fresh


def need_structure_fetch(cache_file: str, ttl_hours: float) -> bool:
    """判断建筑缓存是否过期（需要重新拉取）。"""
    return not _cache_valid(cache_file, ttl_hours)


# ─────────────────────────────────────────────────────────────
# ESI Token 获取
# ─────────────────────────────────────────────────────────────

def get_structure_token(config, repo_root: Path) -> Optional[str]:
    """
    从 config.ini [esi_auth] 段获取有效 Bearer token。
    config: configparser.ConfigParser 对象。
    """
    try:
        from Utilities.esi_auth import get_valid_token as _gvt
    except ImportError:
        # 兼容从 Utilities/ 子目录直接运行的场景
        try:
            from esi_auth import get_valid_token as _gvt
        except ImportError:
            print("[market_order_utils] ✗ 无法导入 esi_auth，请检查 Utilities/esi_auth.py")
            return None

    cache_file_raw = config.get("esi_auth", "token_cache_file",
                                fallback="Cache/Asset/token_cache.json")
    cache_path = Path(cache_file_raw)
    if not cache_path.is_absolute():
        cache_path = repo_root / cache_path

    settings = {
        "client_id":     config.get("esi_auth", "client_id",     fallback=""),
        "client_secret": config.get("esi_auth", "client_secret", fallback=""),
        "redirect_uri":  config.get("esi_auth", "redirect_uri",  fallback=""),
        "scope":         config.get("esi_auth", "scope",         fallback=""),
        "cache_file":    str(cache_path),
        "user_agent":    config.get("esi_auth", "user_agent",    fallback="EVE/1.0"),
    }
    try:
        return _gvt(settings)
    except Exception as e:
        print(f"[market_order_utils] ✗ 获取 token 失败: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# types.json 名称查找（复用 Industry Master 格式）
# ─────────────────────────────────────────────────────────────

_type_index: Optional[Dict[str, int]] = None
_type_index_path: str = ""


def find_type_id(zh_name: str, types_json_path: str) -> Optional[int]:
    """从 types.json 按中文名查找 typeID。"""
    global _type_index, _type_index_path
    if _type_index is None or _type_index_path != types_json_path:
        _type_index = {}
        _type_index_path = types_json_path
        try:
            with open(types_json_path, encoding="utf-8") as f:
                for entry in json.load(f):
                    zh  = (entry.get("zh") or "").strip()
                    tid = entry.get("id")
                    if zh and tid is not None:
                        _type_index[zh] = int(tid)
            print(f"[market_order_utils] types.json 加载: {len(_type_index)} 条目")
        except Exception as e:
            print(f"[market_order_utils] 警告: 无法加载 types.json: {e}")

    return _type_index.get(zh_name) or _type_index.get(zh_name.rstrip("* ").strip())
