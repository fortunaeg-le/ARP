# -*- coding: utf-8 -*-
"""ЭТАП SPEED — кеш разбора entity_types.yaml (и любого другого YAML-конфига).

Семь мест в src/ парсят entity_types.yaml заново на каждом документе (330 раз
за прогон быстрого набора, 9,3% времени — см. docs/JOURNAL.md, C+ часть 3).
Файл за прогон не меняется. Кеш ключуется (путь, mtime) — правка конфига на
диске обязана быть видна следующим вызовом, поэтому mtime входит в ключ, а не
только путь.
"""
import os

import yaml

_cache: dict[tuple[str, float], dict] = {}


def load_yaml_cached(config_path: str) -> dict:
    """yaml.safe_load(config_path) с кешем по (путь, mtime файла)."""
    mtime = os.path.getmtime(config_path)
    key = (config_path, mtime)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    _cache.clear()  # один конфиг за раз в проекте — держать старые mtime незачем
    _cache[key] = config
    return config
