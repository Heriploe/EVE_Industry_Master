"""
utilities/data/app_config.py
============================
统一配置加载工具。

eve_root 通过向上查找 config_meta.json 自动定位，
与目录名称无关（支持 eve/、EVE_Industry_Master/ 等任意命名）。
"""

import json
import sys
from pathlib import Path


def _find_eve_root(start: Path) -> Path:
    """
    从 start 向上查找包含 config_meta.json 的目录，即 eve_root。
    找不到时返回 start.parent.parent.parent（旧行为兜底）。
    """
    cur = start.resolve()
    for _ in range(8):
        if (cur / "config_meta.json").exists():
            return cur
        cur = cur.parent
    return start.resolve().parent.parent.parent


def load_app_config(config_path=None, eve_root=None):
    """
    加载 apps/<app>/config.json，返回 (cfg, eve_root)。

    eve_root 优先级：
      1. 显式传入 eve_root
      2. 向上查找含 config_meta.json 的目录
    """
    if config_path is None:
        try:
            frame = sys._getframe(1)
            caller_file = frame.f_globals.get("__file__")
            if caller_file and caller_file not in ("<string>", ""):
                candidate = Path(caller_file).resolve().parent / "config.json"
                if candidate.exists():
                    config_path = candidate
        except Exception:
            pass

    if config_path is None:
        candidate = Path.cwd() / "config.json"
        if candidate.exists():
            config_path = candidate

    if config_path is None or not Path(config_path).exists():
        raise FileNotFoundError(
            f"未找到 config.json，请将其放在脚本同目录或用 --config 指定。\n"
            f"尝试路径: {config_path}"
        )

    config_path = Path(config_path).resolve()

    with config_path.open(encoding="utf-8") as f:
        cfg = json.load(f)

    if eve_root is not None:
        resolved_root = Path(eve_root).resolve()
    else:
        resolved_root = _find_eve_root(config_path.parent)

    return cfg, resolved_root


def load_meta(eve_root: Path) -> dict:
    """加载 eve_root/config_meta.json，不存在返回空 dict"""
    meta_path = Path(eve_root) / "config_meta.json"
    if not meta_path.exists():
        return {}
    with meta_path.open(encoding="utf-8") as f:
        return json.load(f)


def resolve(eve_root, rel_path: str) -> Path:
    """将相对路径解析为绝对路径（相对于 eve_root）"""
    p = Path(rel_path)
    return p if p.is_absolute() else Path(eve_root) / p


def add_common_args(parser) -> None:
    """添加 --config / --root 通用参数"""
    parser.add_argument("--config", default=None, help="config.json 路径（默认同目录）")
    parser.add_argument("--root",   default=None, help="项目根目录（覆盖自动推断）")
