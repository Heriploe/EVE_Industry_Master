"""
config_utils.py
===============
统一工具：仓库根目录查找、配置读取、路径解析。
所有脚本应从此模块导入 REPO_ROOT，不再各自重复实现查找逻辑。

兼容 Python 3.8+。
"""

import configparser
from pathlib import Path


def find_repo_root(start: Path = None) -> Path:
    """从给定目录（默认本文件所在目录的父目录）向上查找包含 config.ini 的目录。"""
    if start is None:
        start = Path(__file__).resolve().parent.parent
    candidates = [start] + list(start.parents)
    for p in candidates:
        if (p / "config.ini").exists():
            return p
    return start


REPO_ROOT: Path = find_repo_root()


def load_config(repo_root: Path = None) -> configparser.ConfigParser:
    """读取并返回 config.ini 的 ConfigParser 对象。"""
    root = repo_root or REPO_ROOT
    cfg = configparser.ConfigParser()
    cfg.read(str(root / "config.ini"), encoding="utf-8")
    return cfg


def resolve_path(value: str, repo_root: Path = None) -> Path:
    """将相对路径解析为基于 repo_root 的绝对路径；绝对路径直接返回。"""
    root = repo_root or REPO_ROOT
    p = Path(value)
    if not p.is_absolute():
        p = root / p
    return p


def resolve_config_path(
    config: configparser.ConfigParser,
    section: str,
    key: str,
    fallback: str,
    repo_root: Path = None,
) -> Path:
    """从配置文件读取路径值，并解析为绝对路径。"""
    value = config.get(section, key, fallback=fallback)
    return resolve_path(value, repo_root)
