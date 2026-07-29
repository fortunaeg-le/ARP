"""Этап U4 — метрики по разметке и отчёт для разработчика без ПДн.

Главный тест здесь — НА УТЕЧКУ (`test_*_no_marker_*`): документ, все поля
разметки которого нашпигованы заведомо известными маркерными строками, обязан
дать отчёт, где ни одной такой строки нет — ни целиком, ни подстрокой, ни в
другом регистре, ни в json, ни в md. Он идёт первым не по алфавиту, а по
важности: остальные тесты проверяют, что числа верны, а этот — что за верные
числа не заплачено выносом текста документа наружу.

Два уровня, намеренно:
  * ЖИВОЙ сервер (как U1/U3, приёмка U4-6) — реальный конвейер, реальная
    детекция, реальный HTTP. Ловит то, что unit-тест по определению не увидит:
    отчёт, собранный из настоящей разметки настоящего документа.
  * UNIT поверх подменённого хранилища — ловит арифметику метрик с заранее
    известным ответом (приёмка U4-2) и само-тест часового: искусственно
    внесённая в отчёт строка ОБЯЗАНА его уронить. Без само-теста «часовой не
    сработал» и «часового нечему было ловить» неразличимы.
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT / "src"), str(_ROOT / "app")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import report as report_mod  # noqa: E402
import storage  # noqa: E402
import session_store  # noqa: E402

_APP = str(_ROOT / "app")


# --------------------------------------------------------------------------- #
# Маркеры. Ровно приём из постановки: строка, которой заведомо нет ни в одном
# тексте-константе отчёта, подставляется В КАЖДОЕ строковое поле разметки.
# --------------------------------------------------------------------------- #

#
# Маркеры намеренно НЕ содержат обычных слов: проверка ищет любое окно в
# _WINDOW символов, а осмысленное слово внутри маркера («…ОРГАНИЗАЦИИ…»)
# столкнулось бы с прозой самого отчёта («…ни организаций…») и дало бы ложную
# тревогу. Что маркеры действительно непересекаемы с текстами отчёта,
# проверяет test_markers_do_not_collide_with_report_prose — иначе «утечка» и
# «неудачно выбранный маркер» были бы неразличимы.
MARK_PERSON = "УНИКАЛЬНАЯМЕТКА123"
MARK_ORG = "УНИКАЛЬНАЯМЕТКА456"
MARK_ADDR = "УНИКАЛЬНАЯМЕТКА789"
MARK_EMAIL = "unikalnaya.metka@marker-000.test"
ALL_MARKS = (MARK_PERSON, MARK_ORG, MARK_ADDR, MARK_EMAIL)

_WINDOW = 6   # длина подстроки, которую тоже считаем утечкой


def assert_no_marker(haystack: str, marks=ALL_MARKS, where: str = ""):
    """Ни целиком, ни подстрокой (>=_WINDOW символов), ни в другом регистре."""
    low = haystack.lower()
    for mark in marks:
        assert mark.lower() not in low, f"{where}: маркер {mark!r} утёк целиком"
        m = mark.lower()
        for i in range(len(m) - _WINDOW + 1):
            piece = m[i:i + _WINDOW]
            assert piece not in low, f"{where}: подстрока маркера {piece!r} (из {mark!r}) утекла"


# --------------------------------------------------------------------------- #
# Изолированное хранилище для unit-уровня.
# --------------------------------------------------------------------------- #

@pytest.fixture
def iso_store(tmp_path, monkeypatch):
    """Тот же приём, что в tests/test_storage_s1.py: реальный ~/.shifrator не
    трогаем ни на чтение, ни на запись."""
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr(session_store, "_DEFAULT_STORAGE_DIR", sessions_dir)
    monkeypatch.setattr(storage, "default_storage_dir", lambda: sessions_dir)
    return sessions_dir, tmp_path / "markup"


_T0 = datetime(2026, 7, 25, 12, 0, 0).astimezone()


def _entry(kind, *, entity_type="PERSON", old_entity_type=None, old_detector=None,
           value="фрагмент", segment_id="p0", census=None, minutes=0,
           build_mark="u4-test-build"):
    return {
        "kind": kind, "entity_type": entity_type, "segment_id": segment_id,
        "start": 0, "end": len(value), "value": value,
        "old_token": None, "old_segment_id": None, "old_start": None, "old_end": None,
        "old_entity_type": old_entity_type, "old_detector": old_detector,
        "created_at": (_T0 + timedelta(minutes=minutes)).isoformat(),
        "build_mark": build_mark, "applied": False, "apply_error": None,
        "census": census,
    }


_CENSUS_A = {"masks_total": 10,
             "by_type": {"PERSON": 6, "EMAIL": 3, "ORG": 1},
             "by_detector": {"ner": 7, "regex": 3}}


def _seed_known_answer(sid="sess-a"):
    """3 пропущенных, 2 ложных, 1 правка границы, 1 правка типа — при 10 масках."""
    entries = (
        [_entry("missed", entity_type="PERSON", census=_CENSUS_A, minutes=i) for i in range(3)]
        + [_entry("false_mask", entity_type="EMAIL", old_entity_type="EMAIL",
                  old_detector="regex", census=_CENSUS_A, minutes=10 + i) for i in range(2)]
        + [_entry("boundary", entity_type="PERSON", old_entity_type="PERSON",
                  old_detector="ner", census=_CENSUS_A, minutes=20)]
        + [_entry("type", entity_type="PERSON", old_entity_type="ORG",
                  old_detector="ner", census=_CENSUS_A, minutes=21)]
    )
    for e in entries:
        storage.save_markup(sid, e)
    return sid


# --------------------------------------------------------------------------- #
# ПРИЁМКА 2 — метрики считаются ровно с известным ответом.
# --------------------------------------------------------------------------- #

def test_known_answer_totals_and_metrics(iso_store):
    _seed_known_answer()
    r = report_mod.build_report("all")

    t = r["totals"]
    assert (t["missed"], t["false_mask"], t["boundary"], t["type"]) == (3, 2, 1, 1)
    assert t["unknown"] == 0 and t["edits_total"] == 7
    assert t["masks_total"] == 10
    assert r["sample"] == {"documents": 1, "documents_with_denominator": 1,
                           "documents_without_denominator": 0, "edits": 7}

    m = r["metrics"]
    assert m["precision_reviewed_pct"] == 80.0          # найдено 10-2=8 из 10
    assert m["recall_reviewed_pct"] == 72.73            # 8 / (8 + 3 пропущенных)
    assert m["boundary_edit_rate_pct"] == 10.0
    assert m["type_error_rate_pct"] == 10.0


def test_known_answer_by_type(iso_store):
    _seed_known_answer()
    by_type = report_mod.build_report("all")["by_type"]

    person = by_type["PERSON"]
    assert person["masks"] == 6 and person["missed"] == 3 and person["boundary"] == 1
    assert person["precision_reviewed_pct"] == 100.0    # ни одной ложной PERSON-маски
    assert person["recall_reviewed_pct"] == 66.67       # 6 / (6 + 3)

    email = by_type["EMAIL"]
    assert email["masks"] == 3 and email["false_mask"] == 2
    assert email["precision_reviewed_pct"] == 33.33

    # Правка типа отнесена к тому типу, который назвал ДВИЖОК (ORG), а не к
    # исправленному пользователем (PERSON) — иначе ошибка уехала бы в статистику
    # невиновного типа.
    assert by_type["ORG"]["type"] == 1
    assert by_type["PERSON"]["type"] == 0


def test_known_answer_by_detector(iso_store):
    _seed_known_answer()
    by_det = report_mod.build_report("all")["by_detector"]

    assert by_det["regex"]["masks"] == 3 and by_det["regex"]["false_mask"] == 2
    assert by_det["regex"]["precision_reviewed_pct"] == 33.33
    assert by_det["ner"]["masks"] == 7 and by_det["ner"]["false_mask"] == 0
    assert by_det["ner"]["precision_reviewed_pct"] == 100.0
    # Пропуск — это «не сработал НИ ОДИН механизм», отдельная содержательная
    # категория, а не отсутствие данных (инвариант «нет неприменимо»).
    assert by_det["none"]["missed"] == 3
    assert by_det["none"]["masks"] == 0
    assert by_det["none"]["precision_reviewed_pct"] is None


# --------------------------------------------------------------------------- #
# ПРИЁМКА 4 — агрегация по нескольким документам.
# --------------------------------------------------------------------------- #

def test_aggregation_over_several_documents(iso_store):
    _seed_known_answer("sess-a")
    census_b = {"masks_total": 4, "by_type": {"PHONE": 4}, "by_detector": {"regex": 4}}
    for i in range(2):
        storage.save_markup("sess-b", _entry("missed", entity_type="PHONE", census=census_b,
                                              minutes=100 + i, build_mark="u4-next-build"))
    storage.save_markup("sess-b", _entry("false_mask", entity_type="PHONE",
                                          old_entity_type="PHONE", old_detector="regex",
                                          census=census_b, minutes=105,
                                          build_mark="u4-next-build"))

    r = report_mod.build_report("all")
    assert r["sample"]["documents"] == 2
    assert r["totals"]["masks_total"] == 14          # 10 + 4
    assert r["totals"]["missed"] == 5                # 3 + 2
    assert r["totals"]["false_mask"] == 3            # 2 + 1
    assert r["by_type"]["PHONE"]["masks"] == 4
    assert r["by_detector"]["regex"]["masks"] == 7   # 3 + 4

    # Динамика по сборкам (задача 4): разные сборки — разные строки.
    assert set(r["by_build"]) == {"u4-test-build", "u4-next-build"}
    assert r["by_build"]["u4-test-build"]["documents_touched"] == 1
    assert r["by_build"]["u4-next-build"]["missed"] == 2

    # Отчёт по ОДНОМУ документу видит только его.
    only_b = report_mod.build_report("session", "sess-b")
    assert only_b["sample"]["documents"] == 1
    assert only_b["totals"]["missed"] == 2
    assert only_b["totals"]["masks_total"] == 4


def test_document_without_denominator_is_counted_honestly(iso_store):
    """Разметка старше сессии (census нет, сессии нет) не выбрасывается и не
    подделывает знаменатель — документ входит абсолютными числами и явно
    считается «без знаменателя»."""
    storage.save_markup("sess-old", _entry("missed", census=None))
    r = report_mod.build_report("all")
    assert r["sample"]["documents"] == 1
    assert r["sample"]["documents_without_denominator"] == 1
    assert r["totals"]["missed"] == 1
    assert r["totals"]["masks_total"] is None
    assert r["metrics"]["precision_reviewed_pct"] is None


def test_more_false_masks_than_denominator_does_not_go_negative(iso_store):
    """Вырожденный случай: снято масок больше, чем было в слепке (пользователь
    добавил ручные маски и снял их же). precision обязан быть 0, а не −100%."""
    census = {"masks_total": 1, "by_type": {"PERSON": 1}, "by_detector": {"ner": 1}}
    for i in range(3):
        storage.save_markup("sess-neg", _entry("false_mask", entity_type="PERSON",
                                                old_entity_type="PERSON", old_detector="ner",
                                                census=census, minutes=i))
    r = report_mod.build_report("all")
    assert r["metrics"]["precision_reviewed_pct"] == 0.0
    assert r["metrics"]["recall_reviewed_pct"] is None    # ни найденного, ни пропущенного


def test_unknown_kind_is_not_silently_dropped(iso_store):
    """Запись с неизвестным видом правки попадает в «unknown», а не исчезает."""
    storage.save_markup("sess-x", _entry("совершенно новый вид", census=_CENSUS_A))
    r = report_mod.build_report("all")
    assert r["totals"]["unknown"] == 1
    assert r["totals"]["edits_total"] == 1
    assert r["findings"][0]["kind"] == "unknown"


# --------------------------------------------------------------------------- #
# ПРИЁМКА 1 — тест на утечку, unit-уровень: маркер в КАЖДОМ строковом поле.
# --------------------------------------------------------------------------- #

def test_markers_do_not_collide_with_report_prose(iso_store):
    """Санитарная проверка САМИХ маркеров: в ПУСТОМ отчёте (одна проза-константа
    и ноль данных) их быть не может. Если этот тест краснеет — виноват выбор
    маркера, а не утечка; без него ложная тревога выглядела бы как настоящая."""
    empty = report_mod.build_report("all")
    assert empty["sample"]["documents"] == 0
    assert_no_marker(json.dumps(empty, ensure_ascii=False), where="пустой json")
    assert_no_marker(report_mod.render_markdown(empty), where="пустой md")


def test_marker_in_every_markup_field_does_not_reach_report(iso_store):
    storage.save_markup(f"sess-{MARK_PERSON}", {
        "kind": MARK_PERSON, "entity_type": MARK_ORG, "segment_id": MARK_ADDR,
        "start": 0, "end": 5, "value": MARK_PERSON + " " + MARK_ORG + " " + MARK_ADDR,
        "old_entity_type": MARK_ORG, "old_detector": MARK_EMAIL,
        "created_at": _T0.isoformat(), "build_mark": MARK_PERSON,
        "applied": False, "apply_error": MARK_ADDR,
        "census": {"masks_total": 3, "by_type": {MARK_ORG: 3},
                   "by_detector": {MARK_EMAIL: 3}},
    })
    r = report_mod.build_report("all")
    assert_no_marker(json.dumps(r, ensure_ascii=False), where="json отчёта")
    assert_no_marker(report_mod.render_markdown(r), where="md отчёта")

    # Маркеры не «потерялись», а НОРМАЛИЗОВАЛИСЬ в коды словаря — запись видна.
    f = r["findings"][0]
    assert f["kind"] == "unknown" and f["entity_type"] == "unknown"
    assert f["detector"] == "unknown" and f["container"] == "unknown"
    assert f["build"] == "unknown"
    assert f["length_chars"] == len(MARK_PERSON) + len(MARK_ORG) + len(MARK_ADDR) + 2
    assert f["tokens"] == 3


def test_findings_carry_structural_codes_not_text(iso_store):
    """Структурные коды вместо текста: разработчик понимает КЛАСС проблемы —
    тип, механизм, место, длина, признаки — не видя значения."""
    storage.save_markup("sess-s", _entry(
        "missed", entity_type="PERSON", value="Иванов И.И.", segment_id="t2_r3_c1",
        census=_CENSUS_A))
    f = report_mod.build_report("all")["findings"][0]
    assert set(f) == set(report_mod._FINDING_FIELDS), "поля находки — только белый список"
    assert f["container"] == "table_cell"
    assert (f["container_index"], f["row"], f["col"]) == (2, 3, 1)
    assert f["length_chars"] == 11 and f["tokens"] == 2
    assert f["has_initials"] is True and f["has_cyrillic"] is True
    assert f["has_digits"] is False and f["has_latin"] is False
    assert f["case_shape"] == "title"
    assert f["detector"] == "none"    # маски не было — не сработал никто


# --------------------------------------------------------------------------- #
# Само-тест часового: он обязан ЛОВИТЬ, а не просто присутствовать.
# --------------------------------------------------------------------------- #

def test_guard_rejects_free_text_value(iso_store):
    _seed_known_answer()
    r = report_mod.build_report("all")
    r["findings"][0]["value"] = MARK_PERSON        # ровно то, чего боимся
    with pytest.raises(report_mod.ReportLeakError):
        report_mod.assert_structural(r)


def test_guard_rejects_free_text_key(iso_store):
    """Текст мог бы приехать и КЛЮЧОМ словаря (напр. «разбивка по значениям»)."""
    _seed_known_answer()
    r = report_mod.build_report("all")
    r["by_type"][MARK_ORG] = {"masks": 1}
    with pytest.raises(report_mod.ReportLeakError):
        report_mod.assert_structural(r)


def test_guard_rejects_prose_not_from_module_constants(iso_store):
    """Даже безобидная на вид фраза не пройдёт, если её нет в константах модуля —
    иначе «свободный текст» стал бы лазейкой."""
    _seed_known_answer()
    r = report_mod.build_report("all")
    r["limitations"].append("Небольшое уточнение от разработчика.")
    with pytest.raises(report_mod.ReportLeakError):
        report_mod.assert_structural(r)


# --------------------------------------------------------------------------- #
# ЭТАП U4-FIX (находка T1-A) — маркерный тест САМОГО ЧАСОВОГО.
#
# Приём тот же, что у теста на утечку выше: кладём в отчёт заведомые маркеры и
# требуем, чтобы они не прошли. Разница в том, ЧТО проверяется: там — что сборка
# отчёта маркер не кладёт, здесь — что часовой его не пропустит, если положат.
#
# Латинские маркеры подобраны ПО ФОРМЕ слага сборки (`^[A-Za-z0-9][A-Za-z0-9._-]*$`):
# именно они и проходили насквозь до починки — ветка слага применялась к каждой
# строке отчёта, а не только к полю сборки. «dogovor.docx» — имя файла,
# «Ivanov-Petrov» — фамилия транслитом, «sberbank.ru» — домен.
# --------------------------------------------------------------------------- #

#: (описание, строка) — маркер в пяти видах из постановки.
_GUARD_MARKERS = [
    ("маркер целиком",                MARK_PERSON),
    ("маркер в другом регистре",      MARK_PERSON.lower()),
    ("подстрока маркера (6 символов)", MARK_PERSON[3:3 + _WINDOW]),
    ("маркер латиницей (имя файла)",  "dogovor.docx"),
    ("маркер латиницей (транслит)",   "Ivanov-Petrov"),
    ("маркер латиницей (домен)",      "sberbank.ru"),
    ("маркер кириллицей",             MARK_ORG),
]

#: (описание, как внести) — все три способа строке оказаться в отчёте.
_GUARD_PLACES = [
    ("значением поля находки", lambda r, s: r["findings"][0].__setitem__("entity_type", s)),
    ("ключом словаря",         lambda r, s: r["by_type"].__setitem__(s, {"masks": 1})),
    ("элементом списка",       lambda r, s: r["limitations"].append(s)),
    ("новым полем отчёта",     lambda r, s: r.__setitem__(s, 1)),
]


@pytest.mark.parametrize("place", [p[0] for p in _GUARD_PLACES])
@pytest.mark.parametrize("marker", [m[0] for m in _GUARD_MARKERS])
def test_guard_rejects_marker_everywhere(iso_store, marker, place):
    """U4-FIX: маркер обязан быть отсечён В ЛЮБОМ ВИДЕ И В ЛЮБОМ МЕСТЕ отчёта.

    КРАСНОЕ СОСТОЯНИЕ (возврат старого поведения): убрать привязку ветки слага
    к месту, то есть вернуть в `report._string_ok` безусловное
    `if _BUILD_RE.match(s): return True` — тогда все латинские случаи здесь
    зеленеть перестанут (проверено, см. отчёт этапа U4-FIX)."""
    string = dict(_GUARD_MARKERS)[marker]
    mutate = dict(_GUARD_PLACES)[place]

    _seed_known_answer()
    r = report_mod.build_report("all")
    mutate(r, string)
    with pytest.raises(report_mod.ReportLeakError):
        report_mod.assert_structural(r)


def test_guard_still_lets_the_build_slug_through_where_it_belongs(iso_store):
    """Обратная сторона починки: слаг сборки — ЕДИНСТВЕННАЯ строка отчёта, которую
    нельзя перечислить заранее, и в своих трёх местах он обязан проходить. Иначе
    «починка» свелась бы к запрету латиницы вообще и сломала реальный отчёт."""
    _seed_known_answer()
    r = report_mod.build_report("all")
    assert r["tool_build"], "в отчёте обязан быть слаг сборки"
    assert r["findings"][0]["build"] == "u4-test-build"
    assert "u4-test-build" in r["by_build"]
    report_mod.assert_structural(r)          # исходный отчёт проходит

    # ... и ровно тот же слаг НЕ проходит там, где ему не место.
    slug = r["findings"][0]["build"]
    r2 = report_mod.build_report("all")
    r2["scope"] = slug
    with pytest.raises(report_mod.ReportLeakError):
        report_mod.assert_structural(r2)


def test_guard_error_message_does_not_echo_the_offending_text(iso_store):
    """Часовой, ловящий вынос текста, НЕ ИМЕЕТ ПРАВА выносить его сам — ни
    значением, ни ключом. Сообщение уходит на экран через server.py, а оттуда —
    в скриншот, который пользователь отправит разработчику."""
    _seed_known_answer()
    for mutate in (lambda r: r["findings"][0].__setitem__("value", MARK_PERSON),
                   lambda r: r["by_type"].__setitem__(MARK_ORG, {"masks": 1})):
        r = report_mod.build_report("all")
        mutate(r)
        with pytest.raises(report_mod.ReportLeakError) as exc:
            report_mod.assert_structural(r)
        assert_no_marker(str(exc.value), where="текст исключения часового")


def test_build_report_raises_when_guard_trips(iso_store, monkeypatch):
    """Часовой стоит ВНУТРИ build_report: отчёт с утечкой не возвращается вообще,
    а не возвращается «с предупреждением»."""
    _seed_known_answer()
    orig = report_mod._value_shape
    monkeypatch.setattr(report_mod, "_value_shape",
                        lambda v: {**orig(v), "case_shape": str(v)})
    with pytest.raises(report_mod.ReportLeakError):
        report_mod.build_report("all")


# --------------------------------------------------------------------------- #
# ПРИЁМКА 3 и 5 — предпросмотр = выгрузка; ограничения названы В САМОМ отчёте.
# --------------------------------------------------------------------------- #

def test_report_is_deterministic_byte_for_byte(iso_store):
    """Два запроса подряд дают ОДИН И ТОТ ЖЕ байт-в-байт отчёт — иначе
    «предпросмотр совпадает с выгрузкой» ничего не значило бы."""
    _seed_known_answer()
    now = datetime(2026, 7, 25, 15, 30, 0).astimezone()
    a = report_mod.build_report("all", now=now)
    b = report_mod.build_report("all", now=now)
    assert json.dumps(a, ensure_ascii=False, indent=2).encode("utf-8") == \
           json.dumps(b, ensure_ascii=False, indent=2).encode("utf-8")
    assert report_mod.render_markdown(a).encode("utf-8") == \
           report_mod.render_markdown(b).encode("utf-8")


def test_json_and_md_carry_the_same_numbers(iso_store):
    """«Оба по одной схеме» — не декларация: числа обязаны совпасть."""
    _seed_known_answer()
    r = report_mod.build_report("all")
    md = report_mod.render_markdown(r)
    assert f"| Масок поставил движок | {r['totals']['masks_total']} |" in md
    assert f"| пропущено (маски не было) | {r['totals']['missed']} |" in md
    assert f"{r['metrics']['precision_reviewed_pct']}%" in md
    assert f"{r['metrics']['recall_reviewed_pct']}%" in md


def test_limitations_are_named_in_the_report_itself(iso_store):
    """Приёмка 5: ограничения метрики названы В ШАПКЕ отчёта, а не только в
    документации — читатель отчёта документацию не открывает."""
    _seed_known_answer()
    r = report_mod.build_report("all")
    md = report_mod.render_markdown(r)
    assert len(r["limitations"]) >= 5
    joined = " ".join(r["limitations"])
    assert "верхняя оценка" in joined
    assert "не заменяют полный замер" in joined
    for line in r["limitations"]:
        assert line in md, "каждое ограничение обязано попасть в читаемый вид"
    assert "Чего в этом отчёте НЕТ" in md
    assert r["contents"]["excluded"], "отчёт обязан явно говорить, чего в нём нет"


# --------------------------------------------------------------------------- #
# ПРИЁМКА 6 — живой сервер, реальный конвейер, не моки.
# --------------------------------------------------------------------------- #

def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_server(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    port = _free_port()
    env = dict(os.environ, USERPROFILE=str(home), HOME=str(home), SHIFRATOR_UI_PORT=str(port))
    proc = subprocess.Popen(
        [sys.executable, os.path.join(_APP, "server.py")], env=env, cwd=str(_ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        ok = False
        for _ in range(100):
            try:
                with urllib.request.urlopen(base + "/api/ping", timeout=0.5) as r:
                    json.loads(r.read().decode("utf-8"))
                    ok = True
                    break
            except Exception:
                time.sleep(0.1)
        assert ok, "сервер не поднялся вовремя"
        yield base, home
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _req(base, method, path, body=None, headers=None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        else:
            data = body
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _encrypt_sync(base, text: str, filename="doc.txt"):
    started = _req(base, "POST", "/api/encrypt/start", body=text.encode("utf-8"),
                    headers={"X-Filename": filename, "X-Allow-Lossy": "0",
                             "Content-Type": "application/octet-stream"})
    assert started["status"] == "ok", started
    for _ in range(600):
        st = _req(base, "GET", f"/api/encrypt/status?job_id={started['job_id']}")
        assert st["status"] != "error", st
        if st["status"] == "done":
            return st["result"]
        time.sleep(0.1)
    raise AssertionError("encrypt job не завершился вовремя")


_MARKED_DOC = (
    "Стороны договора.\n"
    f"{MARK_PERSON}\n"
    f"{MARK_ORG}\n"
    f"{MARK_ADDR}\n"
    f"Электронная почта: {MARK_EMAIL} для связи.\n"
)


def _mark_line(base, sid, line_index, value, entity_type):
    r = _req(base, "POST", "/api/markup/mark-missed", body={
        "session_id": sid, "segment_id": f"l{line_index}", "start": 0, "end": len(value),
        "entity_type": entity_type,
    })
    assert r["status"] == "ok", r
    return r


def test_live_report_leaks_no_marker_anywhere(live_server):
    """ГЛАВНЫЙ тест приёмки (U4-1). Реальный документ, реальная детекция, все
    четыре вида правки — и ни одного маркера в отчёте."""
    base, _home = live_server
    result = _encrypt_sync(base, _MARKED_DOC)
    assert result["status"] == "ok", result
    sid = result["session_id"]

    # 1-3. Пропущенное: три маркерных строки помечены вручную как ПДн.
    _mark_line(base, sid, 1, MARK_PERSON, "PERSON")
    _mark_line(base, sid, 2, MARK_ORG, "ORG")
    _mark_line(base, sid, 3, MARK_ADDR, "ADDRESS")

    # 4. Ложная маска: e-mail детектируется автоматически — снимаем его.
    m = re.search(r'data-seg="([^"]+)" data-start="(\d+)" data-end="(\d+)" data-token="([^"]+)"',
                  result["anon_html"])
    assert m, "ожидалась хотя бы одна автоматическая маска (e-mail)"
    seg, start, end, token = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
    fp = _req(base, "POST", "/api/markup/false-positive", body={
        "session_id": sid, "segment_id": seg, "start": start, "end": end, "token": token})
    assert fp["status"] == "ok", fp

    for scope, path in (("session", f"/api/report?scope=session&session_id={sid}"),
                        ("all", "/api/report?scope=all")):
        d = _req(base, "GET", path)
        assert d["status"] == "ok", d
        assert_no_marker(d["json_text"], where=f"json_text ({scope})")
        assert_no_marker(d["md_text"], where=f"md_text ({scope})")
        assert_no_marker(json.dumps(d["report"], ensure_ascii=False), where=f"report ({scope})")
        # Отчёт не пустой — иначе «утечки нет» ничего не доказывает.
        assert d["report"]["totals"]["missed"] == 3
        assert d["report"]["totals"]["false_mask"] == 1
        assert len(d["report"]["findings"]) == 4


def test_live_denominator_comes_from_the_real_session(live_server):
    """Знаменатель — не выдумка отчёта: он равен числу масок, которые движок
    реально поставил на этом документе."""
    base, _home = live_server
    result = _encrypt_sync(base, _MARKED_DOC)
    sid = result["session_id"]
    masks_in_document = result["entities_total"]
    assert masks_in_document > 0

    _mark_line(base, sid, 1, MARK_PERSON, "PERSON")
    d = _req(base, "GET", f"/api/report?scope=session&session_id={sid}")
    assert d["report"]["totals"]["masks_total"] == masks_in_document
    assert d["report"]["sample"]["documents_with_denominator"] == 1


def test_live_preview_text_is_exactly_the_downloadable_bytes(live_server):
    """Приёмка 3, серверная половина: json_text — это ровно сериализация того
    объекта, который лежит рядом; md_text повторно выводится из него же.
    (Браузерная половина — что <pre> и скачанный файл берут одну строку — см.
    HANDOFF_U4 §живая проверка.)"""
    base, _home = live_server
    result = _encrypt_sync(base, _MARKED_DOC)
    sid = result["session_id"]
    _mark_line(base, sid, 1, MARK_PERSON, "PERSON")

    d = _req(base, "GET", f"/api/report?scope=session&session_id={sid}")
    assert json.loads(d["json_text"]) == d["report"]
    assert d["json_text"] == json.dumps(d["report"], ensure_ascii=False, indent=2)
    assert d["md_text"] == report_mod.render_markdown(d["report"])


def test_live_report_survives_session_deletion(live_server):
    """Разметка живёт дольше сессии (этап S1) — отчёт обязан считаться и после
    удаления сессии, за счёт слепка знаменателя в самой записи разметки."""
    base, _home = live_server
    result = _encrypt_sync(base, _MARKED_DOC)
    sid = result["session_id"]
    masks_in_document = result["entities_total"]
    _mark_line(base, sid, 1, MARK_PERSON, "PERSON")

    deleted = _req(base, "POST", "/api/session-delete", body={"session_id": sid})
    assert deleted["status"] == "ok" and deleted["deleted"] is True

    d = _req(base, "GET", "/api/report?scope=all")
    assert d["status"] == "ok", d
    assert d["report"]["totals"]["missed"] == 1
    assert d["report"]["totals"]["masks_total"] == masks_in_document
    assert_no_marker(d["json_text"], where="json после удаления сессии")
    assert_no_marker(d["md_text"], where="md после удаления сессии")


def test_live_empty_report_is_valid_not_an_error(live_server):
    """Пилот открывает «Аналитику» до первой правки — экран обязан показать
    пустой, но корректный отчёт, а не ошибку."""
    base, _home = live_server
    d = _req(base, "GET", "/api/report?scope=all")
    assert d["status"] == "ok", d
    assert d["report"]["sample"]["documents"] == 0
    assert d["report"]["totals"]["masks_total"] is None
    assert d["report"]["findings"] == []
    assert "Правок нет" in d["md_text"]
