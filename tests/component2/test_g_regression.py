"""Группа G — регресс.

Компонент 2 не должен ломать существующие 138 тестов блоков 1-7, и модуль
file_detokenizer обязан оставаться "лёгким" по импорту (без natasha/NER),
как заявлено в HANDOFF_12.
"""
import subprocess
import sys
import textwrap

import pytest

_PROJECT_ROOT = None


def _project_root():
    global _PROJECT_ROOT
    if _PROJECT_ROOT is None:
        import os
        _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return _PROJECT_ROOT


def test_existing_block1_7_suite_still_fully_green():
    """Прогоняет tests/ (без component2/) отдельным процессом pytest.

    Если тут появится хоть один упавший тест — это регресс, вызванный
    Компонентом 2 (новые модули этой сессии тестов ничего в блоках 1-7 не
    меняли), а не повод редактировать существующие тесты.

    Дочерний pytest получает тот же фильтр -m "not slow", что и родительский
    прогон (см. pytest.ini): без него дочерний процесс сам пытается собрать и
    прогнать медленные тесты (полный корпус) и упирается в timeout этого
    subprocess.run — тест НЕ падал явно, а просто выпадал из дефолтного
    прогона молча (см. HANDOFF_STAGE_0D.md). Фильтр обязателен именно здесь,
    а не только в pytest.ini родителя, потому что дочерний pytest запускается
    как отдельный процесс и addopts родителя на него не наследуются.

    БЮДЖЕТ ВРЕМЕНИ (этап O2, находка U4-G-TIMEOUT закрыта). Было жёстко 300 c —
    и это перестало быть бюджетом: вложенный прогон дорос до 488 c, тест падал
    по SubprocessTimeout на ЧИСТОМ HEAD, на простаивающей машине. Оптимизация
    пайплайна (этап O2) вернула вложенный прогон к 295 c, то есть в старый
    лимит он снова укладывается — но с запасом в 5 секунд, что запасом не
    является: чуть более медленная машина, фоновая нагрузка или один новый тест
    снова красят набор без единого дефекта в продукте.

    Новый лимит — 900 c: примерно ТРОЙНОЙ запас к измеренным 295 c. Он переживает
    вдвое более медленный ноутбук (~590 c) и рост набора, но остаётся КОНЕЧНЫМ и
    ловит то, ради чего таймаут и стоит, — зависший дочерний pytest. Лимит здесь
    НЕ сторож производительности: за неё отвечает замер O2, а не этот тест.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--ignore=tests/component2",
         "-m", "not slow", "-q"],
        cwd=_project_root(),
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert result.returncode == 0, (
        "Регресс в существующем наборе блоков 1-7 (не трогать эти тесты, "
        "чинить причину в коде):\n"
        f"--- stdout ---\n{result.stdout[-4000:]}\n--- stderr ---\n{result.stderr[-2000:]}"
    )


def test_import_file_detokenizer_does_not_pull_natasha():
    code = textwrap.dedent(
        """
        import sys
        import file_detokenizer  # noqa: F401
        assert "natasha" not in sys.modules, sorted(m for m in sys.modules if "natasha" in m)
        assert "ner_detector" not in sys.modules
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_project_root(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


def test_import_file_detokenizer_is_fast():
    code = textwrap.dedent(
        """
        import time
        t0 = time.time()
        import file_detokenizer  # noqa: F401
        elapsed = time.time() - t0
        assert elapsed < 0.5, f"import file_detokenizer занял {elapsed:.3f}с (ожидалось < 0.5с)"
        print("OK", elapsed)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_project_root(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout
