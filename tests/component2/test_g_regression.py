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

    Лимит 900 c (тройной запас к измеренным этапом O2 295 c) продержался до
    2026-08-05 и снова перестал быть бюджетом: замер на рабочей машине под
    ОБЫЧНОЙ фоновой нагрузкой дал **1995 c** при зелёном дочернем прогоне
    (1272 passed) — тест падал по SubprocessTimeout без единого дефекта в
    продукте. Разгрузить машину принципиально нельзя, поэтому калибровка идёт
    по фактическому замеру, а не по идеальным условиям.

    Текущий лимит — 4000 c: двойной запас к измеренным 1995 c. Он остаётся
    КОНЕЧНЫМ и ловит то, ради чего таймаут и стоит, — зависший дочерний pytest.
    Лимит здесь НЕ сторож производительности: за неё отвечает замер O2, а не
    этот тест.

    `-n 0` ЗАКРЕПЛЯЕТ дочерний прогон за ОДНИМ процессом (этап TESTOPT добавил
    `-n auto` в addopts верхнего pytest.ini — дочерний pytest читает тот же
    файл и без явного `-n 0` попытался бы САМ разъехаться на воркеры). Бюджет
    калиброван по ОДНОПРОЦЕССНОМУ прогону (2026-08-05, 1995 c) — распараллелив
    дочерний прогон, мы обесценили бы эту калибровку без нового замера, а сам
    вложенный запуск ужe исполняется ВНУТРИ одного воркера верхнего прогона:
    заставлять его плодить ещё 10+ своих воркеров — риск исчерпания памяти
    (natasha на процесс, см. JOURNAL TESTOPT часть 1) без измеренной выгоды.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--ignore=tests/component2",
         "-m", "not slow", "-q", "-n", "0"],
        cwd=_project_root(),
        capture_output=True,
        text=True,
        timeout=4000,
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
    """Файловый путь восстановления не тянет за собой тяжёлый слой детекции.

    Проверка по существу — в тесте выше (`..._does_not_pull_natasha`): в
    sys.modules не должно оказаться natasha/ner_detector. Здесь — грубый
    страж того же по времени: подтянись тяжёлый слой, импорт вырос бы в разы.

    ПОРОГ. Было 0.5 c. Замер 2026-08-05 на рабочей машине под ОБЫЧНОЙ фоновой
    нагрузкой показал, что порог недостижим в принципе и тест краснеет без
    единого дефекта в продукте.

    ВАЖНО ПРО МЕТОД ЗАМЕРА (ошибка, которую не стоит повторять): одиночный
    замер вне набора даёт 0.525 / 0.538 / 0.526 c и вводит в заблуждение.
    Тест исполняется ВНУТРИ полного прогона под `-n auto`, конкурируя с
    28-39 воркерами xdist и с однопроцессным дочерним прогоном соседнего
    теста, — и там тот же импорт занимает **2.120 c**, вчетверо больше.
    Калибровать этот порог по одиночному запуску нельзя.

    Текущий порог 5 c — запас ~2.4x к измеренным в реальных условиях 2.120 c
    (ранее наблюдалось и 1.719 c). Профиль импорта вне нагрузки: ~0.6 c, из
    них основное — session_store -> cryptography.fernet, json, models,
    tempfile. Порог грубый намеренно: подтянись сюда natasha, импорт вырос бы
    на порядок, а точную проверку делает тест выше по sys.modules.
    """
    code = textwrap.dedent(
        """
        import time
        t0 = time.time()
        import file_detokenizer  # noqa: F401
        elapsed = time.time() - t0
        assert elapsed < 5, f"import file_detokenizer занял {elapsed:.3f}с (ожидалось < 5с)"
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
