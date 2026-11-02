# -*- coding: utf-8 -*-
"""配置与历史记录的本地存储（操作有记忆，可一键复用上次设置）"""
import json
import os
import time

from . import APP_NAME

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_ROOT, "data")
_CONFIG = os.path.join(DATA_DIR, "config.json")
_HISTORY = os.path.join(DATA_DIR, "history.json")


def _ensure():
    if not os.path.isdir(DATA_DIR):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
        except Exception:
            pass


def _read(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, data):
    _ensure()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_config() -> dict:
    return _read(_CONFIG, {})


def save_config(data: dict) -> None:
    _write(_CONFIG, data)


def load_history() -> list:
    data = _read(_HISTORY, [])
    return data if isinstance(data, list) else []


def add_history(record: dict, limit: int = 30) -> None:
    data = load_history()
    record.setdefault("time", time.strftime("%Y-%m-%d %H:%M:%S"))
    data.insert(0, record)
    _write(_HISTORY, data[:limit])


def clear_history() -> None:
    _write(_HISTORY, [])


def dir_size_mb(path: str) -> float:
    """统计目录体积（用于界面上显示程序大小）。"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except Exception:
                pass
    return total / 1024.0 / 1024.0


def latest_mtime(path: str):
    """目录内最新修改时间（只看代码与资源，忽略 data 运行时文件）。"""
    latest = 0.0
    skip = {"data", "__pycache__"}
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith((".pyc", ".log", ".json")):
                continue
            try:
                latest = max(latest, os.path.getmtime(os.path.join(root, f)))
            except Exception:
                pass
    if latest <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(latest))
