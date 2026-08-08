# -*- coding: utf-8 -*-
"""ЭТАП SPEED — кеш разбора entity_types.yaml (и любого другого YAML-конфига).

Семь мест в src/ парсят entity_types.yaml заново на каждом документе (330 раз
за прогон быстрого набора, 9,3% времени — см. docs/JOURNAL.md, C+ часть 3).
Файл за прогон не меняется. Кеш ключуется (путь, mtime) — правка конфига на
диске обязана быть видна следующим вызовом, поэтому mtime входит в ключ, а не
только путь.
"""
import os
import sys

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

def default_config_path() -> str:
    """Путь к корневому `entity_types.yaml` — ОДИНАКОВО в рабочем дереве и в
    собранной программе.

    ЭТАП FIX-3, найдено КРУГОМ СОБРАННОЙ ПРОГРАММЫ (не тестами и не корпусом).
    `__file__`-арифметика («на уровень вверх от src/») в PyInstaller-сборке
    указывает ВНУТРЬ бандла, где конфига нет: `packaging/shifrator.spec` кладёт
    его в `sys._MEIPASS`. Следствие было не «не нашли файл», а МОЛЧАЛИВЫЙ откат
    на умолчания — порядок арбитража и составы наборов теряли все фабричные
    типы, и собранный exe ОТКАЗЫВАЛСЯ шифровать любой документ
    (ArbitrationContractError). Ту же загадку уже решал `app/paths.py`; здесь
    она сведена для библиотеки, которой на `app/` смотреть нельзя."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", "")
        if base:
            frozen = os.path.join(base, "entity_types.yaml")
            if os.path.exists(frozen):
                return frozen
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "entity_types.yaml")
