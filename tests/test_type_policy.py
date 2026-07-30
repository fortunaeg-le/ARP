# -*- coding: utf-8 -*-
"""ЭТАП T1 — переключатель типов данных: зеркала контракта.

Главное здесь — не «профиль вернул список», а ДВА структурных утверждения:
  * фильтр на этапе МАСКИРОВАНИЯ не двигает границы масок остальных типов;
  * фильтр в ДЕТЕКЦИИ их двигает (то есть выключать там — дефект, а не оптимизация).
Второе доказывается положительно: тест ПАДАЕТ, если границы вдруг перестанут
зависеть от барьеров, — тогда обоснование этапа больше не верно и его надо
переписать, а не молча оставить.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "app"))

import type_policy  # noqa: E402
from models import SourceDocument, TextSegment  # noqa: E402
from pipeline import run_detection  # noqa: E402
from tokenizer import tokenize, resolve_for_masking, apply_masking  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")

# Документ с соседством адрес↔реквизит↔телефон — ровно тот случай, ради которого
# фильтр стоит после разрешения пересечений.
SAMPLE = (
    "Адрес: г. Москва, ул. Ленина, д. 5, кв. 12, ИНН 7707083893, тел. +7 (495) 123-45-67. "
    "Иванов Иван Иванович, СНИЛС 112-233-445 95, e-mail ivanov@example.com, сумма 5 000 руб."
)


def _doc(text=SAMPLE):
    return SourceDocument(
        segments=[TextSegment(id="p0", text=text, source_type="txt_line", metadata={})],
        source_format="txt", source_path="t.txt",
    )


def _mask_map(kept):
    """Маски как множество координат: (segment_id, start, end, тип, токен)."""
    return sorted((e.segment_id, e.start, e.end, e.entity_type, e.token) for e in kept)


# --------------------------------------------------------------------------- #
# Наборы
# --------------------------------------------------------------------------- #

def test_default_profile_is_personal_only():
    assert type_policy.DEFAULT_PROFILE == type_policy.PROFILE_PERSONAL
    assert set(type_policy.PERSONAL) == {
        "PERSON", "ADDRESS", "PHONE", "EMAIL", "PASSPORT", "SNILS", "BIRTHDATE",
        # ЭТАП T2-INN: ИНН физлица (12 цифр) — персональные данные, и с
        # разделением типов он входит в набор по умолчанию.
        "INN_PERSON",
    }


def test_profiles_are_nested_and_maximum_is_everything():
    assert set(type_policy.PERSONAL) < set(type_policy.PERSONAL_REQUISITES)
    assert set(type_policy.PERSONAL_REQUISITES) < set(type_policy.WITH_MONEY)
    # ЭТАП T2: набор «всё, включая деньги» расширен процентом и сроком —
    # решение владельца назвало три вида данных одним пунктом (см. обоснование
    # у type_policy.WITH_MONEY).
    assert (set(type_policy.WITH_MONEY) - set(type_policy.PERSONAL_REQUISITES)
            == {"SUM", "PERCENT", "TERM"})
    assert type_policy.MAXIMUM is None
    # «Максимум» ⊇ «всё, включая деньги» — и с запасом на будущие типы.
    known = set(type_policy.known_types(CONFIG))
    assert set(type_policy.WITH_MONEY) <= known


def test_inn_of_a_person_is_personal_data_and_inn_of_a_company_is_not():
    """ЭТАП T2-INN. До него в этом файле стоял обратный тест: «ИНН — ОДИН тип,
    поэтому набор 1 не может его включать». Та формулировка описывала не
    решение, а ОГРАНИЧЕНИЕ, ценой которого был открытый ИНН предпринимателя.
    Ограничение снято разделением типов, и контракт перевёрнут:

      * `INN`        — 10 цифр, ИНН ОРГАНИЗАЦИИ, персональными данными НЕ
                       является, живёт в наборе 2 «данные и реквизиты»;
      * `INN_PERSON` — 12 цифр, ИНН ЧЕЛОВЕКА, персональные данные, живёт в
                       наборе 1 «только персональные данные».

    Тест краснеет и в обратную сторону: если типы снова сольют в один или
    перепутают местами в наборах, это будет видно здесь, а не на документе
    пользователя."""
    import yaml
    with open(CONFIG, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)["entity_types"]
    inn_keys = sorted(k for k in cfg if k.startswith("INN"))
    assert inn_keys == ["INN", "INN_PERSON"], (
        "типов ИНН должно быть ровно два — организации (10 цифр) и человека (12)")
    assert "INN_PERSON" in type_policy.PERSONAL
    assert "INN" not in type_policy.PERSONAL
    assert "INN" in type_policy.PERSONAL_REQUISITES
    # Длина — не комментарий, а часть конфига: паттерны обязаны различаться
    # именно числом цифр, иначе типы разошлись бы только на словах.
    assert "{9}" in cfg["INN"]["pattern"] and "{11}" in cfg["INN_PERSON"]["pattern"]
    # У каждой длины СВОЯ контрольная сумма (шаг 0б: алгоритмы разные).
    assert cfg["INN"]["validate"] == "inn10_checksum"
    assert cfg["INN_PERSON"]["validate"] == "inn12_checksum"


def test_resolve_overrides_switch_single_type_both_ways():
    known = type_policy.known_types(CONFIG)
    on = type_policy.resolve("personal", {"SUM": True}, known=known)
    assert "SUM" in on and "ORG" not in on
    off = type_policy.resolve("with_money", {"PHONE": False}, known=known)
    assert "PHONE" not in off and "SUM" in off
    # Выключение поверх «Максимума» тоже обязано работать.
    m = type_policy.resolve("maximum", {"ORG": False}, known=known)
    assert m is not None and "ORG" not in m and "PERSON" in m


def test_maximum_without_overrides_is_the_no_filter_sentinel():
    assert type_policy.resolve("maximum", {}, known=type_policy.known_types(CONFIG)) is None


# --------------------------------------------------------------------------- #
# Файл настроек: битый вход не роняет программу
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw", [
    '{"profile": "personal", "types": {"НЕТ_ТАКОГО_ТИПА": true}}',
    '{"profile": "такого_набора_нет"}',
    '{"types": "не словарь"}',
    "не json вовсе {{{",
    "[]",
    "",
])
def test_broken_settings_never_crash(tmp_path, raw):
    p = tmp_path / "settings.json"
    p.write_text(raw, encoding="utf-8")
    s = type_policy.load_settings(p)
    assert s["profile"] in type_policy.PROFILES
    enabled = type_policy.resolve(s["profile"], s["types"],
                                  known=type_policy.known_types(CONFIG))
    assert enabled is None or enabled  # состав получен, исключения не было


def test_missing_settings_file_gives_default_profile(tmp_path):
    s = type_policy.load_settings(tmp_path / "нет-такого-файла.json")
    assert s == {"profile": type_policy.DEFAULT_PROFILE, "types": {}}


def test_unknown_type_in_settings_is_ignored_not_fatal(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"profile": "personal",
                             "types": {"ВЫДУМАННЫЙ": True, "SUM": True}}),
                 encoding="utf-8")
    enabled = type_policy.enabled_types(CONFIG, settings=type_policy.load_settings(p))
    assert "ВЫДУМАННЫЙ" not in enabled
    assert "SUM" in enabled


def test_document_with_broken_settings_still_gets_masked(tmp_path):
    """Приёмка 4: файл настроек с несуществующим типом не роняет программу —
    не на уровне парсера, а на уровне полного прохода документа."""
    p = tmp_path / "settings.json"
    p.write_text('{"profile": "нет", "types": {"НЕТ": false, "PHONE": true}}', encoding="utf-8")
    enabled = type_policy.enabled_types(CONFIG, settings=type_policy.load_settings(p))
    doc = _doc()
    anon, kept = tokenize(doc, run_detection(doc, CONFIG), CONFIG, enabled_types=enabled)
    assert kept and "[PHONE_1]" in anon


# --------------------------------------------------------------------------- #
# Контракт этапа: фильтр стоит в маскировании
# --------------------------------------------------------------------------- #

def test_maximum_is_byte_identical_to_no_filter():
    doc = _doc()
    ents = run_detection(doc, CONFIG)
    a_text, a_kept = tokenize(doc, ents, CONFIG, enabled_types=None)
    b_text, b_kept = tokenize(_doc(), run_detection(_doc(), CONFIG), CONFIG,
                              enabled_types=frozenset(type_policy.known_types(CONFIG)))
    assert a_text == b_text
    assert _mask_map(a_kept) == _mask_map(b_kept)


@pytest.mark.parametrize("victim", sorted(set(type_policy.WITH_MONEY)))
def test_disabling_a_type_does_not_move_any_other_boundary(victim):
    """Приёмка 2 в миниатюре (полная таблица — на корпусе): выключение типа
    убирает ЕГО маски и не двигает границы и токены остальных."""
    doc = _doc()
    kept_all = resolve_for_masking(doc, run_detection(doc, CONFIG), CONFIG)
    _, full = apply_masking(doc, kept_all, CONFIG, None)
    base = [m for m in _mask_map(full) if m[3] != victim]

    kept_all2 = resolve_for_masking(_doc(), run_detection(_doc(), CONFIG), CONFIG)
    known = set(type_policy.known_types(CONFIG))
    _, cut = apply_masking(_doc(), kept_all2, CONFIG, frozenset(known - {victim}))
    assert all(m[3] != victim for m in _mask_map(cut))
    assert _mask_map(cut) == base


def test_disabling_in_detection_would_move_neighbour_boundaries():
    """ОБОСНОВАНИЕ этапа, положительный тест. Выключенный тип работает БАРЬЕРОМ:
    убери реквизиты из детекции — и адрес разрастётся на них. Если этот тест
    когда-нибудь начнёт падать, значит барьеров больше нет и §ШАГ 1 задания надо
    переписывать, а не игнорировать."""
    from anchor_registry import detect_structural, suppress_conflicts
    from ner_detector import detect_ner
    from regex_detector import detect_regex

    doc = _doc("Адрес: г. Москва, ул. Ленина, д. 5, кв. 12, ИНН 7707083893")
    regex_e = detect_regex(doc, CONFIG)
    struct = detect_structural(doc, regex_entities=regex_e)
    org = [e for e in struct if e.entity_type == "ORG"]
    per = [e for e in struct if e.entity_type == "PERSON"]

    def addr(regex_barriers):
        ner = suppress_conflicts(struct, detect_ner(
            doc, CONFIG, regex_entities=regex_barriers, org_entities=org, per_entities=per))
        return sorted((e.start, e.end) for e in ner if e.entity_type == "ADDRESS")

    with_barrier = addr(regex_e)
    without_barrier = addr([])            # «ИНН выключен в детекции»
    assert with_barrier and without_barrier
    assert with_barrier != without_barrier, (
        "адрес перестал зависеть от барьера реквизитов — обоснование этапа T1 устарело"
    )
    # Конкретно: без барьера адрес СЪЕДАЕТ цифры ИНН (граница уезжает вправо).
    assert without_barrier[0][1] > with_barrier[0][1]


def test_filter_does_not_shift_token_numbering_of_other_types():
    """Нумерация ведётся отдельным счётчиком на префикс, поэтому выпадение целого
    типа не сдвигает номера чужих масок (иначе приёмка 2 была бы невыполнима)."""
    doc = _doc()
    kept_all = resolve_for_masking(doc, run_detection(doc, CONFIG), CONFIG)
    _, full = apply_masking(doc, kept_all, CONFIG, None)
    tokens_full = {(e.entity_type, e.start): e.token for e in full if e.entity_type != "PHONE"}

    kept_all2 = resolve_for_masking(_doc(), run_detection(_doc(), CONFIG), CONFIG)
    known = set(type_policy.known_types(CONFIG))
    _, cut = apply_masking(_doc(), kept_all2, CONFIG, frozenset(known - {"PHONE"}))
    tokens_cut = {(e.entity_type, e.start): e.token for e in cut}
    assert tokens_cut == tokens_full


# --------------------------------------------------------------------------- #
# Умолчание конечного продукта (реальный процесс, реальный файл настроек)
# --------------------------------------------------------------------------- #

def _cli_encrypt(tmp_path, text, settings=None):
    import subprocess

    home = tmp_path / "home"
    if settings is not None:
        (home / ".shifrator").mkdir(parents=True, exist_ok=True)
        (home / ".shifrator" / "settings.json").write_text(settings, encoding="utf-8")
    src = tmp_path / "d.txt"
    src.write_text(text, encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               USERPROFILE=str(home), HOME=str(home))
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "shifrator.py"), "encrypt", str(src)],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    sid = proc.stdout.strip()
    return (home / ".shifrator" / "sessions" / f"{sid}.txt").read_text(encoding="utf-8")


_CLI_TEXT = "ИНН 7707083893, e-mail ivanov@example.com, сумма 5 000 руб."


def test_default_run_masks_personal_data_only(tmp_path):
    """Умолчание продукта с этапа T1 — «только персональные данные»: e-mail
    спрятан, ИНН и сумма НАЙДЕНЫ, но не замаскированы."""
    anon = _cli_encrypt(tmp_path, _CLI_TEXT)
    assert "[EMAIL_1]" in anon
    assert "7707083893" in anon and "[INN_" not in anon
    assert "5 000 руб." in anon and "[SUM_" not in anon


def test_settings_file_switches_the_set(tmp_path):
    anon = _cli_encrypt(tmp_path, _CLI_TEXT, settings='{"profile": "with_money"}')
    assert "[EMAIL_1]" in anon and "[INN_1]" in anon and "[SUM_1]" in anon


def test_single_type_override_on_top_of_the_set(tmp_path):
    anon = _cli_encrypt(tmp_path, _CLI_TEXT,
                        settings='{"profile": "personal", "types": {"INN": true}}')
    assert "[INN_1]" in anon and "[EMAIL_1]" in anon
    assert "[SUM_" not in anon


def test_settings_file_with_unknown_type_does_not_crash_the_cli(tmp_path):
    """Приёмка 4 на РЕАЛЬНОМ процессе: несуществующий тип в файле настроек
    игнорируется, encrypt отрабатывает и печатает session_id."""
    anon = _cli_encrypt(
        tmp_path, _CLI_TEXT,
        settings='{"profile": "personal", "types": {"КРИПТОКОШЕЛЁК": true, "SUM": true}}')
    assert "[EMAIL_1]" in anon and "[SUM_1]" in anon


# --------------------------------------------------------------------------- #
# Отчёт программы (шаг 4) и его сторож
# --------------------------------------------------------------------------- #

def test_describe_lists_enabled_and_disabled():
    d = type_policy.describe(CONFIG, frozenset(type_policy.PERSONAL))
    assert set(d["enabled_types"]) == set(type_policy.PERSONAL)
    assert "SUM" in d["disabled_types"] and "ORG" in d["disabled_types"]
    assert set(d["enabled_types"]) | set(d["disabled_types"]) == set(type_policy.known_types(CONFIG))
    # «Максимум» -> всё включено, ничего не выключено.
    dm = type_policy.describe(CONFIG, None)
    assert dm["disabled_types"] == []


def test_report_guard_is_red_on_a_field_outside_the_whitelist():
    """Приёмка 3, КРАСНОЕ состояние сторожа: поле, не внесённое в белый список,
    роняет сборку отчёта. Ровно этим сторож и опасен для шага 4 — новое поле
    состава надо было в список внести."""
    import report

    ok = {"policy": {"profile": "personal", "enabled_types": ["PERSON"],
                     "disabled_types": ["SUM"], "documents_without_policy": 0}}
    report.assert_structural(ok)  # внесено в _SCHEMA_KEYS — проходит

    with pytest.raises(report.ReportLeakError):
        report.assert_structural({"policy": {"состав_типов": ["PERSON"]}})
    with pytest.raises(report.ReportLeakError):
        report.assert_structural({"policy": {"profile": "мой_личный_набор"}})


def test_report_guard_should_also_catch_a_latin_field_outside_the_whitelist():
    """НАХОДКА T1-A — ЗАКРЫТА этапом U4-FIX, метка xfail снята.

    Было: `_string_ok` пропускал любую строку, совпавшую с `_BUILD_RE`
    (`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`), — ветка заведена под слаг сборки, но
    применялась к КАЖДОЙ строке отчёта и к каждому ключу. Поэтому поле, которого
    нет ни в одном белом списке, проходило сторожа, если названо латиницей; и,
    что важнее, латинское имя файла («dogovor.docx») в качестве ЗНАЧЕНИЯ тоже
    прошло бы. Кириллический текст документа сторож ловил, латинский — нет.

    Стало: ветка слага привязана к МЕСТУ в схеме (`tool_build`,
    `findings[].build`, ключи `by_build`). Полный маркерный набор —
    `tests/test_u4_report.py::test_guard_rejects_marker_everywhere`."""
    import report

    with pytest.raises(report.ReportLeakError):
        report.assert_structural({"totally_new_field": 1})
    with pytest.raises(report.ReportLeakError):
        report.assert_structural({"scope": "dogovor.docx"})


def test_report_policy_block_says_mixed_when_documents_disagree():
    import report

    def doc(pol):
        return {"entries": [{}], "census": {"policy": pol} if pol else None}

    p1 = {"profile": "personal", "enabled_types": ["PERSON"], "disabled_types": ["SUM"]}
    p2 = {"profile": "maximum", "enabled_types": ["PERSON", "SUM"], "disabled_types": []}

    same = report._policy_block([doc(p1), doc(p1)])
    assert same["profile"] == "personal" and same["enabled_types"] == ["PERSON"]

    mixed = report._policy_block([doc(p1), doc(p2)])
    assert mixed["profile"] == report.MIXED and mixed["enabled_types"] is None

    partial = report._policy_block([doc(p1), doc(None)])
    assert partial["profile"] == report.MIXED and partial["documents_without_policy"] == 1

    none = report._policy_block([doc(None)])
    assert none["profile"] == report.UNKNOWN and none["enabled_types"] is None
