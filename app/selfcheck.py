"""Проверка окружения при старте (этап U1).

Цель — не дать пользователю увидеть питоновский трейсбек. Каждая проверка
переводит техническую причину в человеческую фразу; полная техническая
картина (исключение, traceback) уходит ТОЛЬКО в лог-файл рядом с приложением.

Проверки:
  1. Модели/словари (navec/slovnet под Natasha, словарь pymorphy2) — единственный
     надёжный способ убедиться, что они на месте и не повреждены, — реально
     прогнать через них короткий синтетический текст ТЕМ ЖЕ конвейером
     (`pipeline.run_detection`), которым обрабатываются документы пользователя.
     Проверка файлов по путям дала бы ложное "ОК" на повреждённом архиве.
  2. Права на запись в рабочую папку (сессии).
  3. Свободное место на диске.

Публичная функция:
  - run() -> CheckReport
"""

import os
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import app_root  # noqa: E402

_ROOT = app_root()
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(1, _SRC)

_MIN_FREE_BYTES = 200 * 1024 * 1024  # 200 МБ — модели уже ~50 МБ, плюс сессии/логи

_SMOKE_TEXT = (
    "ООО «Ромашка» в лице генерального директора Иванова Ивана Ивановича, "
    "ИНН 7707083893, адрес: г. Москва, ул. Ленина, д. 5, кв. 10, "
    "тел. +7 (495) 123-45-67, действующего на основании Устава."
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    human_message: str
    detail: str = ""


@dataclass
class CheckReport:
    results: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def failures(self):
        return [r for r in self.results if not r.ok]


def _log_path() -> Path:
    """Лог-файл рядом с приложением (exe при сборке, app/ в разработке).

    При отсутствии прав на запись туда (нештатно для --onedir в профиле
    пользователя, но не невозможно) откатываемся во временную папку ОС —
    лог не должен сам стать причиной падения self-check.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    candidate = base / "shifrator_startup.log"
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        with open(candidate, "a", encoding="utf-8"):
            pass
        return candidate
    except OSError:
        return Path(tempfile.gettempdir()) / "shifrator_startup.log"


def _log(message: str) -> None:
    path = _log_path()
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(message.rstrip("\n") + "\n")
    except OSError:
        pass  # логирование не должно ронять self-check


def _check_models() -> CheckResult:
    try:
        from pipeline import run_detection
        from models import SourceDocument, TextSegment

        doc = SourceDocument(
            segments=[TextSegment(id="p0", text=_SMOKE_TEXT,
                                   source_type="docx_paragraph", metadata={})],
            source_format="txt",
            source_path="<selfcheck>",
        )
        config_path = os.path.join(_ROOT, "entity_types.yaml")
        entities = run_detection(doc, config_path)
        if not entities:
            # Конвейер отработал без исключений, но не нашёл НИЧЕГО на тексте,
            # который заведомо содержит ИНН/ФИО/адрес — модели молча деградировали.
            _log("selfcheck: pipeline вернул 0 сущностей на смоук-тексте — подозрение "
                 "на повреждённые данные моделей")
            return CheckResult(
                "models", False,
                "Модели распознавания повреждены или неполны — обработка документов "
                "невозможна. Переустановите программу заново, полностью распаковав "
                "поставку в новую папку.",
            )
        return CheckResult("models", True, "Модели распознавания загружены.")
    except Exception as exc:  # noqa: BLE001 — единственная граница трейсбека
        _log(f"selfcheck: models FAILED: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        return CheckResult(
            "models", False,
            "Не удалось загрузить модели распознавания (файлы моделей отсутствуют "
            "или повреждены). Переустановите программу заново, полностью распаковав "
            "поставку в новую папку, ничего не удаляя из неё.",
            detail=str(exc),
        )


def _check_storage_writable(storage_dir: Path) -> CheckResult:
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        probe = storage_dir / ".selfcheck_probe.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return CheckResult("storage_write", True, "Рабочая папка доступна для записи.")
    except OSError as exc:
        _log(f"selfcheck: storage_write FAILED ({storage_dir}): {type(exc).__name__}: {exc}")
        return CheckResult(
            "storage_write", False,
            f"Нет прав на запись в рабочую папку программы:\n{storage_dir}\n"
            "Проверьте, что папка не доступна только для чтения и не заблокирована "
            "антивирусом.",
            detail=str(exc),
        )


def _check_disk_space(storage_dir: Path) -> CheckResult:
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(storage_dir).free
    except OSError as exc:
        _log(f"selfcheck: disk_space FAILED ({storage_dir}): {type(exc).__name__}: {exc}")
        return CheckResult(
            "disk_space", False,
            "Не удалось проверить свободное место на диске.", detail=str(exc),
        )
    if free < _MIN_FREE_BYTES:
        mb = free // (1024 * 1024)
        return CheckResult(
            "disk_space", False,
            f"Мало свободного места на диске (осталось {mb} МБ, нужно хотя бы "
            f"{_MIN_FREE_BYTES // (1024 * 1024)} МБ). Освободите место и запустите "
            "программу снова.",
        )
    return CheckResult("disk_space", True, "Свободного места достаточно.")


def run() -> CheckReport:
    """Прогоняет все проверки, возвращает отчёт. Ничего не бросает наружу."""
    from storage import default_storage_dir

    storage_dir = default_storage_dir()
    results = [
        _check_models(),
        _check_storage_writable(storage_dir),
        _check_disk_space(storage_dir),
    ]
    report = CheckReport(results=results)
    if not report.ok:
        _log(f"selfcheck: FAILED ({len(report.failures())} проверок) — "
             f"лог: {_log_path()}")
    return report
