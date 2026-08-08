# -*- coding: utf-8 -*-
"""Этап B — structure-first PER (люди) + консолидация точек входа.

Три группы:
  * Фронт-1 (тест-договор): подпись «Ковалёв И. П.», которая до этапа B утекала, теперь
    замаскирована; полное ФИО в косвенном падеже — ОДИН спан.
  * ПАРИТЕТ CLI↔UI: один файл через pipeline.run_detection из CLI (shifrator.py,
    subprocess) и через UI-путь (app/core.py, в процессе) → анонимный текст ПОБАЙТНО
    идентичен. Структурная гарантия консолидации: копии порядка шагов больше нет.
  * PER-точность: омоним-фамилия/предлог/улица метятся правильно (guard), вторичный
    ключ «имя+отчество» накрывает осиротевшее продолжение ФИО.
"""
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
_APP = os.path.join(_ROOT, "app")
for _p in (_SRC, _APP):
    if _p not in sys.path:
        sys.path.insert(1, _p)

from models import SourceDocument, TextSegment  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize  # noqa: E402
import session_store  # noqa: E402
import storage  # noqa: E402

_CFG = os.path.join(_ROOT, "entity_types.yaml")


def _anon(text):
    # Как .txt-извлечение: каждая НЕПУСТАЯ строка — отдельный сегмент (l0, l1, …).
    # Так «ДОГОВОР\nООО «Восход»» не склеивается в один сегмент (в реальном документе
    # это разные абзацы), а косвенный падеж ФИО внутри одной строки — один спан.
    segs = [
        TextSegment(id=f"l{i}", text=line, source_type="txt_line", metadata={})
        for i, line in enumerate(text.split("\n")) if line.strip()
    ]
    doc = SourceDocument(segments=segs, source_format="txt", source_path="<t>")
    ents = run_detection(doc, _CFG)
    anon, final = tokenize(doc, ents, _CFG)
    return anon, final


def _per(final):
    return [e.original_text for e in final if e.entity_type == "PERSON"]


# --------------------------------------------------------------------------- #
#                       ФРОНТ-1: синтетический договор                         #
# --------------------------------------------------------------------------- #
_DOGOVOR = (
    "ДОГОВОР ПОСТАВКИ\n"
    "ООО «Восход», именуемое в дальнейшем «Поставщик», в лице генерального "
    "директора Ковалёва Ивана Петровича, действующего на основании устава, с одной "
    "стороны, и гражданка Морозова Анна Николаевна, с другой стороны, заключили "
    "настоящий договор.\n"
    "Уведомления направляются Морозовой Анне Николаевне по адресу регистрации.\n"
    "Подписи сторон:\n"
    "Поставщик: _______________ / Ковалёв И. П. /\n"
    "Покупатель: _______________ / Морозова А. Н. /\n"
)


def test_front1_signature_initials_masked():
    """Подпись «Ковалёв И. П.» (фамилия + инициалы у линии подписи) — замаскирована."""
    anon, final = _anon(_DOGOVOR)
    assert "Ковалёв" not in anon, anon
    assert any("Ковалёв И. П." == p for p in _per(final)), _per(final)


def test_front1_oblique_full_name_single_span():
    """Полное ФИО в косвенном падеже «Морозовой Анне Николаевне» — ОДИН спан целиком."""
    anon, final = _anon(_DOGOVOR)
    per = _per(final)
    assert "Морозовой Анне Николаевне" in per, per
    # не разорвано на «Морозовой» + «Анне Николаевне»
    assert "Морозовой" not in [p for p in per if p != "Морозовой Анне Николаевне"]
    assert "Морозов" not in anon


def test_front1_org_stays_single_org():
    """«Восход» остаётся ЕДИНЫМ ORG — PER-арбитраж его не перетипировал в человека."""
    anon, final = _anon(_DOGOVOR)
    orgs = [e.original_text for e in final if e.entity_type == "ORG"]
    assert any("Восход" in o for o in orgs), orgs
    assert "Восход" not in anon


def test_front1_director_and_citizen_masked():
    """ФИО директора («в лице …») и гражданки («гражданка …») — замаскированы целиком."""
    anon, final = _anon(_DOGOVOR)
    assert "Морозова Анна Николаевна" in _per(final)
    assert "Ковалёва Ивана Петровича" in _per(final)
    for leak in ("Морозова", "Ковалёв", "Ивана", "Николаевне", "Анне"):
        assert leak not in anon, (leak, anon)


# --------------------------------------------------------------------------- #
#                        PER-точность (guard/омонимы)                          #
# --------------------------------------------------------------------------- #
def test_preposition_po_not_marked():
    """Предлог «по» (у pymorphy околонулевой разбор-фамилия «По») НЕ метится PER и не
    тянет хвост ФИО-прогона."""
    anon, final = _anon("Ивановой Анне Петровне по телефону сообщить решение")
    per = _per(final)
    assert "Ивановой Анне Петровне" in per, per
    assert all(p == "Ивановой Анне Петровне" for p in per), per


def test_street_surname_not_marked_person():
    """Улица-однофамилец («ул. Пушкина», «ш. Гагарина») — не PER (адресный тип-маркер)."""
    _, final = _anon("Адрес: г. Москва, ул. Пушкина, д. 5, ш. Гагарина")
    assert _per(final) == []


def test_homonym_surname_needs_anchor():
    """Голая фамилия-омоним обычного слова без якоря НЕ метится (Aprime-1 PER)."""
    _, final = _anon("Договор заключён. Мороз ударил внезапно за окном.")
    assert _per(final) == []


def test_registered_person_propagates_by_case():
    """Зарегистрированный человек распространяется проходом 2 по падежам (косвенный)."""
    _, final = _anon("Гражданин Беляев Олег Олегович обязуется. Позже Беляеву передадут акт.")
    per = _per(final)
    assert "Беляев Олег Олегович" in per
    assert "Беляеву" in per


def test_namepatr_secondary_key_covers_orphan_continuation():
    """Вторичный ключ (имя, отчество): осиротевшее «Имя Отчество» в СОСЕДНЕМ сегменте
    (cross-segment split) накрывается тем же человеком."""
    segs = [
        TextSegment(id="l0", text="Получатель: Сидоров", source_type="txt_line", metadata={}),
        TextSegment(id="l1", text="Михаил Михайлович", source_type="txt_line", metadata={}),
        TextSegment(id="l2", text="в лице Сидорова Михаила Михайловича, действующего на основании",
                    source_type="txt_line", metadata={}),
    ]
    doc = SourceDocument(segments=segs, source_format="txt", source_path="<t>")
    ents = run_detection(doc, _CFG)
    anon, _ = tokenize(doc, ents, _CFG)
    assert "Михаил Михайлович" not in anon, anon
    assert "Сидоров" not in anon, anon


# --------------------------------------------------------------------------- #
#                        ПАРИТЕТ CLI ↔ UI (побайтно)                           #
# --------------------------------------------------------------------------- #
_PARITY_TEXT = (
    "Договор подписан между ООО «Восход», ИНН 7707083893, в лице директора "
    "Ковалёва Ивана Петровича, и гражданкой Морозовой Анной Николаевной, "
    "проживающей по адресу г. Казань, ул. Баумана, д. 44. "
    "Контакты: тел. 8-900-123-45-67, e-mail test@example.com. "
    "ИП Пирогова А.С. привлекается как перевозчик. Подпись: / Ковалёв И. П. /"
)


def test_cli_ui_parity_byte_identical(tmp_path, monkeypatch):
    """Один файл → анонимный текст из CLI (shifrator.py, subprocess) и из UI-пути
    (app/core.run_encrypt, в процессе) ПОБАЙТНО совпадает. Обе точки входа зовут
    pipeline.run_detection — расхождения быть не может по построению; тест это
    ЗАКРЕПЛЯЕТ (регресс UI-рассинхрона)."""
    src = tmp_path / "doc.txt"
    src.write_text(_PARITY_TEXT, encoding="utf-8")

    home = tmp_path / "home"
    # ЭТАП T1: тест про ПАРИТЕТ конвейеров и про то, что ORG/PER маскируются, —
    # набор пришпилен к «Максимуму» (умолчание с T1 — только персональные данные,
    # ORG в него не входит). Обе точки входа читают ОДИН и тот же файл настроек,
    # так что паритет проверяется вместе с политикой, а не в обход неё.
    from testlib_policy import pin_all_types
    pin_all_types(home)
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               USERPROFILE=str(home), HOME=str(home))

    # --- CLI-путь: реальный процесс shifrator.py encrypt ---
    enc = subprocess.run(
        [sys.executable, os.path.join(_ROOT, "shifrator.py"), "encrypt", str(src)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=_ROOT,
    )
    assert enc.returncode == 0, enc.stderr
    sid = enc.stdout.strip()
    # ЭТАП STORE: {sid}.txt зашифрован — читаем расшифровкой, иначе паритет
    # сравнивал бы шифротекст со строкой.
    from testlib_store import read_anon_text
    cli_txt = read_anon_text(home / ".shifrator" / "sessions", sid)

    # --- UI-путь: app/core.run_encrypt в том же изолированном HOME ---
    # ЭТАП STORE: заплатка снята. Раньше здесь стояли два monkeypatch.setattr —
    # session_store хранил путь константой, вычисленной на импорте, и подмена
    # USERPROFILE/HOME ниже на неё не действовала: save_session писал в РЕАЛЬНЫЙ
    # ~/.shifrator того, кто гоняет тесты. Теперь default_storage_dir() читает
    # Path.home() при вызове, и подмены окружения довольно.
    # Сохраняем ИСХОДНЫЕ значения переменных (sentinel для отсутствующих) и точно
    # восстанавливаем — иначе tmp-HOME протёк бы в последующие тесты (default_storage_dir).
    _MISSING = object()
    saved = {k: os.environ.get(k, _MISSING) for k in ("USERPROFILE", "HOME")}
    os.environ["USERPROFILE"] = str(home)
    os.environ["HOME"] = str(home)
    try:
        import importlib
        import core as app_core
        importlib.reload(app_core)
        result = app_core.run_encrypt(str(src))
    finally:
        for k, v in saved.items():
            if v is _MISSING:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    ui_txt = result["anon_text"]

    assert cli_txt == ui_txt, (
        "CLI и UI дали РАЗНЫЙ анонимный текст (рассинхрон конвейера):\n"
        f"CLI={cli_txt!r}\nUI ={ui_txt!r}"
    )
    # и обе точки реально что-то замаскировали (не пустой прогон)
    assert "[PERSON_" in cli_txt and "[ORG_" in cli_txt and "Ковалёв" not in cli_txt
