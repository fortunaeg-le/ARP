# -*- coding: utf-8 -*-
"""ЭТАП T1-UI — экран выбора набора типов.

Этап НЕ трогает механизм фильтрации (он принят этапом T1 и закрыт тестами
`tests/test_type_policy.py`), а даёт к нему доступ из интерфейса. Поэтому здесь
проверяется ровно три вещи:

  1. однократное решение при первом запуске версии (обновление против первой
     установки) — `type_policy.ensure_settings`;
  2. экран: состав наборов и типов приходит ИЗ `type_policy`, а сохранение идёт
     в ТОТ ЖЕ файл `~/.shifrator/settings.json` (второго хранилища нет);
  3. выбор на экране меняет РЕАЛЬНЫЙ результат обезличивания.

Домашняя директория во всех тестах подменена (`monkeypatch.setenv("HOME"...)`),
и `Path.home()` берёт её — иначе тест писал бы настройки живому пользователю.
"""
import json
import os
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "src"), os.path.join(_ROOT, "app")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import type_policy  # noqa: E402
import session_store  # noqa: E402
import storage  # noqa: E402

CONFIG = os.path.join(_ROOT, "entity_types.yaml")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Подменённая домашняя директория. USERPROFILE — для Windows, HOME — для
    POSIX; `Path.home()` смотрит на разные переменные на разных ОС."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)
    assert Path.home() == tmp_path
    # ЭТАП STORE: заплатка снята. Раньше здесь стояли два monkeypatch.setattr —
    # session_store хранил путь константой, вычисленной на импорте, и подмены
    # переменных выше на него не действовали: core.run_encrypt писал сессию в
    # РЕАЛЬНЫЙ ~/.shifrator. Теперь default_storage_dir() спрашивает Path.home()
    # при каждом вызове, и подмены окружения достаточно — этот тест заодно её и
    # проверяет (сессия обязана лечь под tmp_path).
    return tmp_path


# --------------------------------------------------------------------------- #
# ШАГ 1. Первая установка против обновления
# --------------------------------------------------------------------------- #

def test_fresh_install_gets_the_personal_default(home):
    """Приёмка 1: пустой домашний каталог -> «только персональные данные»."""
    res = type_policy.ensure_settings()
    assert res == {"created": True, "profile": type_policy.PROFILE_PERSONAL}
    written = json.loads((home / ".shifrator" / "settings.json").read_text(encoding="utf-8"))
    assert written == {"profile": "personal", "types": {}}
    assert type_policy.enabled_types(CONFIG) == frozenset(type_policy.PERSONAL)


@pytest.mark.parametrize("trace", [
    "sessions/key.bin",
    "sessions/3f2a1c9e-0000-0000-0000-000000000000.enc",
    "markup/2026-07-29.jsonl",
])
def test_upgrade_keeps_the_previous_behaviour_maximum(home, trace):
    """Приёмка 2: файла настроек нет, но следы прошлой работы есть -> «Максимум».

    Это и есть «прежнее поведение»: до этапа T1 маскировалось всё. Каждый след
    проверяется по отдельности — достаточно любого одного.
    """
    p = home / ".shifrator" / trace
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")

    res = type_policy.ensure_settings()
    assert res == {"created": True, "profile": type_policy.PROFILE_MAXIMUM}
    assert type_policy.enabled_types(CONFIG) is type_policy.MAXIMUM


def test_launcher_lock_alone_is_not_a_trace_of_previous_work(home):
    """`launcher.lock` создаёт САМ запуск интерфейса — по нему первая установка
    выглядела бы обновлением. Список следов закрытый, и это его смысл."""
    d = home / ".shifrator"
    d.mkdir(parents=True)
    (d / "launcher.lock").write_text("{}", encoding="utf-8")

    assert type_policy.has_previous_work(d) is False
    assert type_policy.ensure_settings()["profile"] == type_policy.PROFILE_PERSONAL


def test_empty_sessions_dir_is_not_a_trace(home):
    """Пустую папку могла создать неудавшаяся попытка — работой она не является."""
    (home / ".shifrator" / "sessions").mkdir(parents=True)
    assert type_policy.has_previous_work() is False


def test_existing_settings_file_is_never_overwritten(home):
    """Приёмка 3: выбор пользователя уважается, решение принимается ОДИН раз."""
    d = home / ".shifrator"
    d.mkdir(parents=True)
    mine = {"profile": "with_money", "types": {"EMAIL": False}}
    (d / "settings.json").write_text(json.dumps(mine), encoding="utf-8")
    # следы работы есть — и всё равно ничего не переписывается
    (d / "sessions").mkdir()
    (d / "sessions" / "key.bin").write_bytes(b"x")

    res = type_policy.ensure_settings()
    assert res == {"created": False, "profile": "with_money"}
    assert json.loads((d / "settings.json").read_text(encoding="utf-8")) == mine


def test_broken_settings_file_is_left_alone_and_does_not_crash(home):
    """Приёмка 6: битый файл — это СУЩЕСТВУЮЩИЙ файл. Мы его не перезаписываем
    (это чужие данные, их мог править человек) и не падаем: `load_settings`
    сводит его к умолчанию, самому узкому набору."""
    d = home / ".shifrator"
    d.mkdir(parents=True)
    (d / "settings.json").write_text("{не json", encoding="utf-8")

    assert type_policy.ensure_settings() == {"created": False,
                                             "profile": type_policy.DEFAULT_PROFILE}
    assert (d / "settings.json").read_text(encoding="utf-8") == "{не json"
    assert type_policy.enabled_types(CONFIG) == frozenset(type_policy.PERSONAL)


def test_settings_file_with_unknown_type_does_not_crash_the_screen(home):
    """Приёмка 6, вторая половина: несуществующий тип и несуществующий набор в
    файле не роняют ЭКРАН (не только парсер)."""
    import core

    d = home / ".shifrator"
    d.mkdir(parents=True)
    (d / "settings.json").write_text(
        json.dumps({"profile": "нет_такого", "types": {"НЕТ_ТАКОГО": True, "EMAIL": False}}),
        encoding="utf-8",
    )
    view = core.settings_view(CONFIG)
    assert view["profile"] == type_policy.DEFAULT_PROFILE
    assert "НЕТ_ТАКОГО" not in [t["type"] for t in view["types"]]
    assert next(t for t in view["types"] if t["type"] == "EMAIL")["enabled"] is False


# --------------------------------------------------------------------------- #
# ШАГ 2. Экран
# --------------------------------------------------------------------------- #

def test_screen_shows_all_four_sets_with_a_human_hint(home):
    import core

    view = core.settings_view(CONFIG)
    assert [p["id"] for p in view["profiles"]] == list(type_policy.PROFILES)
    for p in view["profiles"]:
        assert p["label"] and p["hint"]


def test_screen_type_list_comes_from_the_config_not_from_a_second_copy(home):
    """Экран не держит своего списка типов: он обязан совпасть с составом
    конфига (минус типы с ВЫКЛЮЧЕННЫМ детектором) — иначе разойдётся при
    появлении нового типа.

    ЭТАП DATE-ON. Здесь стояло «минус DATE» ИМЕНЕМ, и ровно то же имя стояло в
    `app/core.py`. Пока детектор даты молчал, обе записи были верны; в день,
    когда владелец дату включил, экран продолжил бы прятать тип, который
    маскирует текст ПО УМОЛЧАНИЮ, — и ни один страж этого не увидел бы, потому
    что тест сверял одно зашитое имя с другим. Теперь обе стороны читают состав
    из конфига, а имя не упоминается ни разу."""
    import core

    view = core.settings_view(CONFIG)
    off = type_policy.detector_off_types(CONFIG)
    expected = [t for t in type_policy.known_types(CONFIG) if t not in off]
    assert [t["type"] for t in view["types"]] == expected
    assert all(t["label"] != t["type"] or t["type"] in ("INN", "OGRN", "KPP", "BIK")
               for t in view["types"])
    # Регресс-якорь этапа DATE-ON: дата ВКЛЮЧЕНА и обязана быть на экране.
    assert "DATE" not in off
    assert "DATE" in [t["type"] for t in view["types"]]


def test_screen_hides_a_type_whose_detector_is_switched_off(home, tmp_path):
    """ЖИВАЯ ПРОБА механизма (он же красное состояние теста выше): тип с
    `enabled: false` исчезает с экрана САМ, без правки `app/core.py`. Сегодня
    выключенных типов нет ни одного, поэтому проверка идёт на своём конфиге —
    иначе механизм остался бы без стража в тот самый момент, когда им
    перестали пользоваться."""
    import core

    src = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    src["entity_types"]["EMAIL"]["enabled"] = False
    cfg = tmp_path / "off.yaml"
    cfg.write_text(yaml.safe_dump(src, allow_unicode=True), encoding="utf-8")

    assert "EMAIL" in type_policy.known_types(str(cfg))       # маскировать умеет
    assert type_policy.detector_off_types(str(cfg)) == {"EMAIL"}
    view = core.settings_view(str(cfg))
    assert "EMAIL" not in [t["type"] for t in view["types"]]
    assert "PHONE" in [t["type"] for t in view["types"]]      # соседи на месте


def test_screen_saves_into_the_same_file_the_mechanism_reads(home):
    """Второго места хранения настроек нет: пишем экраном — читает `type_policy`."""
    import core

    core.save_settings("personal_requisites", {"SUM": True}, config_path=CONFIG)
    path = home / ".shifrator" / "settings.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["profile"] == "personal_requisites"
    assert raw["types"] == {"SUM": True}
    assert type_policy.enabled_types(CONFIG) == frozenset(
        type_policy.PERSONAL_REQUISITES + ("SUM",))


def test_screen_does_not_store_overrides_that_repeat_the_set(home):
    """Галочка, совпадающая с набором, — не выбор пользователя, а шум: она
    залипла бы при следующей смене набора. В файл идёт только отличие."""
    import core

    all_on = {t: True for t in type_policy.PERSONAL}
    core.save_settings("personal", all_on, config_path=CONFIG)
    raw = json.loads((home / ".shifrator" / "settings.json").read_text(encoding="utf-8"))
    assert raw["types"] == {}


# --------------------------------------------------------------------------- #
# ШАГ 3. Строка «чем обезличено»
# --------------------------------------------------------------------------- #

def test_result_line_says_the_set_is_not_full_and_names_what_was_left(home):
    import core

    s = core.policy_summary(
        {"profile": "personal",
         "enabled_types": list(type_policy.PERSONAL),
         "disabled_types": ["ORG", "INN", "DATE"]},
        config_path=CONFIG,
    )
    assert s["is_full"] is False
    assert s["profile_label"] == type_policy.PROFILE_LABELS["personal"]
    # ЭТАП DATE-ON: «Дата» БОЛЬШЕ НЕ ЗАМАЛЧИВАЕТСЯ. Пока её детектор стоял
    # выключенным, назвать её среди «вы выключили» было бы неправдой — маски
    # не появились бы ни при какой галочке. Теперь детектор работает, значит
    # выключение — настоящий выбор человека, и строка обязана о нём сказать.
    assert s["disabled_labels"] == ["Организация", "ИНН организации (10 цифр)", "Дата"]
    assert "ФИО" in s["enabled_labels"]


def test_result_line_stays_silent_about_a_type_whose_detector_is_off(home, tmp_path):
    """Обратная сторона того же механизма: выключенный ДЕТЕКТОРОМ тип в перечне
    «осталось намеренно» не называется — человек его не выключал."""
    import core

    src = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    src["entity_types"]["EMAIL"]["enabled"] = False
    cfg = tmp_path / "off.yaml"
    cfg.write_text(yaml.safe_dump(src, allow_unicode=True), encoding="utf-8")

    s = core.policy_summary(
        {"profile": "personal", "enabled_types": [],
         "disabled_types": ["EMAIL", "ORG"]},
        config_path=str(cfg),
    )
    assert s["disabled_labels"] == ["Организация"]


def test_result_line_on_the_maximum_set_says_full(home):
    import core

    s = core.policy_summary({"profile": "maximum",
                             "enabled_types": list(type_policy.known_types(CONFIG)),
                             "disabled_types": []}, config_path=CONFIG)
    assert s["is_full"] is True
    assert s["disabled_labels"] == []


# --------------------------------------------------------------------------- #
# ПРИЁМКА 4. Выбор на экране меняет РЕАЛЬНЫЙ результат обезличивания
# --------------------------------------------------------------------------- #

_DOC = (
    "Договор с ООО «Восход», ИНН 7707083893.\n"
    "Директор Петров Иван Сергеевич, телефон +7 916 123-45-67.\n"
)


def _encrypt(txt_path):
    """Реальный путь UI-шифрации (та же функция, что зовёт сервер)."""
    import core
    return core.run_encrypt(txt_path, config_path=CONFIG)


def _tokens(result):
    return sorted({r["entity_type"] for r in result["replacements"]})


def test_turning_a_type_on_and_off_changes_the_masks(home, txt_factory):
    """Приёмка 4: включили тип — маски появились, выключили — исчезли.
    Проверяется на РЕАЛЬНОМ обезличивании, а не на парсере настроек."""
    import core

    path = txt_factory(_DOC, filename="dogovor.txt")

    core.save_settings("personal", {}, config_path=CONFIG)
    narrow = _encrypt(path)
    assert "INN" not in _tokens(narrow)
    assert "PERSON" in _tokens(narrow)
    assert narrow["policy"]["is_full"] is False
    # ЭТАП T2-INN: в наборе «только персональные данные» выключен ИНН
    # ОРГАНИЗАЦИИ, а ИНН человека, наоборот, включён — подпись обязана
    # называть именно тот, что остался в тексте.
    assert "ИНН организации (10 цифр)" in narrow["policy"]["disabled_labels"]
    assert "ИНН человека (12 цифр)" in narrow["policy"]["enabled_labels"]

    core.save_settings("personal", {"INN": True}, config_path=CONFIG)
    with_inn = _encrypt(path)
    assert "INN" in _tokens(with_inn)
    assert "7707083893" not in with_inn["anon_text"]
    assert "7707083893" in narrow["anon_text"]

    core.save_settings("maximum", {}, config_path=CONFIG)
    full = _encrypt(path)
    assert {"INN", "PERSON", "ORG"} <= set(_tokens(full))
    assert full["policy"]["is_full"] is True


# --------------------------------------------------------------------------- #
# ПРИЁМКА 5. Экран не влияет на замер
# --------------------------------------------------------------------------- #

def test_measurement_ignores_the_user_settings_file(home, txt_factory):
    """Урезанный набор в файле настроек не должен менять НИ ОДНОЙ цифры замера:
    харнесс зовёт токенизацию с `type_policy.MAXIMUM` явно и файл не читает.

    Сравнение побайтное — по анонимизированному тексту и карте масок.
    """
    import core
    from extractor import extract
    from pipeline import run_detection
    from tokenizer import tokenize

    path = txt_factory(_DOC, filename="measure.txt")
    doc = extract(path)
    entities = run_detection(doc, CONFIG)

    def measure():
        # ТОЧНО так же, как tests/corpus/run_measurement.py
        text, ents = tokenize(doc, entities, CONFIG, enabled_types=type_policy.MAXIMUM)
        return text, [(e.entity_type, e.token, e.original_text) for e in ents]

    core.save_settings("maximum", {}, config_path=CONFIG)
    before = measure()

    core.save_settings("personal", {"EMAIL": False, "PHONE": False}, config_path=CONFIG)
    assert type_policy.enabled_types(CONFIG) != type_policy.MAXIMUM   # настройка реально урезана
    after = measure()

    assert before == after
