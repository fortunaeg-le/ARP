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


@pytest.mark.slow
def test_existing_block1_7_suite_still_fully_green():
    """Прогоняет tests/ (без component2/) отдельным процессом pytest.

    Если тут появится хоть один упавший тест — это регресс, вызванный
    Компонентом 2 (новые модули этой сессии тестов ничего в блоках 1-7 не
    меняли), а не повод редактировать существующие тесты.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--ignore=tests/component2", "-q"],
        cwd=_project_root(),
        capture_output=True,
        text=True,
        timeout=300,
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
