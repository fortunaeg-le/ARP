"""Корневой conftest: делает библиотечные модули из src/ импортируемыми
плоскими именами (from models import …) во всех тестах — и в tests/, и в
tests/component2/. Существующие тестовые файлы менять не требуется.

Продублировано editable-установкой пакета в venv (pip install -e .): она
покрывает сабпроцессы вида `python -c "import file_detokenizer"`
(tests/component2/test_g_regression.py), до которых conftest не дотягивается.
"""
import os
import sys

_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
