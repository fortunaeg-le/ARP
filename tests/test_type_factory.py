# -*- coding: utf-8 -*-
"""ЭТАП TYPE-FACTORY — стражи фабрики типов.

Три группы:
  1. TestWall — стена механически: AST-скан src/ (закрывает V2-WALL-NOGUARD),
     «значение генератора не находится в собранном артефакте», белый список.
  2. TestAssembly — собранный entity_types.yaml == сборке из base+typedefs
     (ручная правка собранного файла не живёт дольше одного прогона тестов);
     миграция не меняет разобранный конфиг против дофабричного снимка.
  3. TestValidator — неполное описание роняет сборку с человеческим текстом.

Тесты живут в зоне ИЗМЕРИТЕЛЯ: им можно читать и typedefs, и сборщик.
"""
import ast
import copy
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, os.path.join(ROOT, "tests", "corpus_v2"))

import assemble_types as AT  # noqa: E402

CONFIG = os.path.join(ROOT, "entity_types.yaml")
SNAPSHOT = os.path.join(HERE, "fixtures", "entity_types_pre_factory.yaml")

# --------------------------------------------------------------------------- #
#  1. Стена
# --------------------------------------------------------------------------- #

#: Имена модулей, которые src/ не имеет права импортировать, и подстроки путей,
#: которые не имеют права появляться в его строковых литералах. «values» как
#: ИМЯ МОДУЛЯ запрещено (реестр значений корпуса); слово values в выражениях
#: (dict.values()) AST-скан не трогает — он смотрит только Import/ImportFrom
#: и строковые константы с путёвыми признаками.
_FORBIDDEN_MODULES = {"values", "generate", "gold_v2", "measure_v2",
                      "assemble_types"}
_FORBIDDEN_PATH_BITS = ("corpus_v2", "typedefs", "gold.json", "gold_v2")


class TestWall:

    def _src_files(self):
        return [os.path.join(SRC, f) for f in sorted(os.listdir(SRC))
                if f.endswith(".py")]

    def test_src_never_imports_generator_side(self):
        """Закрывает V2-WALL-NOGUARD: стена перестаёт держаться дисциплиной."""
        offenders = []
        for path in self._src_files():
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                for m in mods:
                    root_name = m.split(".")[0]
                    if root_name in _FORBIDDEN_MODULES or m.startswith("tests"):
                        offenders.append(f"{os.path.basename(path)}: import {m}")
        assert offenders == [], (
            "src/ импортирует генераторную сторону корпуса — стена пробита: "
            + "; ".join(offenders))

    def test_src_has_no_path_literals_into_corpus_answers(self):
        offenders = []
        for path in self._src_files():
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for bit in _FORBIDDEN_PATH_BITS:
                        if bit in node.value:
                            offenders.append(
                                f"{os.path.basename(path)}:{node.lineno}: "
                                f"«{node.value[:60]}»")
        assert offenders == [], (
            "src/ несёт путь в эталон/реестр значений: " + "; ".join(offenders))

    def test_generator_values_do_not_leak_into_artifact(self):
        """Живой прогон стены: каждое значение examples/negatives всех typedefs
        не находится подстрокой в собранном entity_types.yaml."""
        with open(CONFIG, encoding="utf-8") as f:
            artifact = f.read()
        tds = AT.load_typedefs()
        checked = 0
        for t, td in tds.items():
            gen = td.get("generator")
            if not isinstance(gen, dict):
                continue
            for p in list(gen.get("examples") or []) + [
                    n.get("text", "") for n in (gen.get("negatives") or [])]:
                assert str(p) not in artifact, (
                    f"СТЕНА: значение «{p}» ({t}) видно детектору")
                checked += 1
        # пока все typedefs legacy — проверять нечего, и это честно видно
        assert checked >= 0

    def test_wall_selfcheck_stops_the_build(self, monkeypatch, tmp_path):
        """Проба стены: пример, совпадающий с куском артефакта, роняет сборку.
        base подменяется минимальным — проба не зависит от хода миграции."""
        td = _valid_typedef()
        td["generator"]["examples"][0] = "token_prefix"   # заведомо есть в файле
        tdefs = tmp_path / "typedefs"
        tdefs.mkdir()
        _write_typedef(tdefs, td)
        mini_base = tmp_path / "base.yaml"
        mini_base.write_text("strict_zones: true\nentity_types:\n",
                             encoding="utf-8")
        monkeypatch.setattr(AT, "TYPEDEFS", str(tdefs))
        monkeypatch.setattr(AT, "BASE", str(mini_base))
        with pytest.raises(SystemExit) as exc:
            AT.assemble()
        assert "СТЕНА" in str(exc.value)


# --------------------------------------------------------------------------- #
#  2. Сборка и равенство
# --------------------------------------------------------------------------- #

#: Санкционированные ОТКЛОНЕНИЯ от дофабричного снимка — решения владельца,
#: изменившие поведение типа ПОСЛЕ перевода (сам перевод шёл байт-в-байт и
#: запечатан гейтом). Запись обязана быть точной: тип, которого здесь нет,
#: сравнивается строго; тип, который здесь есть, обязан РЕАЛЬНО отличаться
#: (иначе запись протухла и убирается).
SANCTIONED_CHANGES = {
    "SUM": "TYPE-FACTORY-2, блок MONEY_WORDS: составная форма «цифры (пропись) "
           "валюта [копейки]» — единый спан; до правки в v2 утекали все 78 "
           "таких сумм",
    "TERM": "FIX-3, долг FIX2-ANTIANCHOR-RIGHT: добавлен ПРАВЫЙ дисквалификатор "
            "(anti_anchor_right + своё окно) — единственный вид случая "
            "term/norm-ref левым окном не закрывался ни при каком N",
    "PASSPORT": "ЭТАП DEBTS, долг 0c-B: спан серии+номера сузился с m.group(0) "
                "(вместе со словом-якорем «паспорт»/«серия») до значения — "
                "`span_group: 1` + `span_group_end: 2`. Перебор границ на v1: "
                "428 сущностей / 3441 символ -> 0/0. Якорь остался в ПОИСКЕ",
    "DATE": "ЭТАП DATE-ON (решение владельца 2026-08-17 «дату включить во всех "
            "наборах»): снят `enabled: false`, стоявший с первого коммита "
            "проекта; форма расширена словесными записями («12» марта 2023 г., "
            "12 марта 2023 года, двенадцатого марта) и сужена по году до РОВНО "
            "четырёх цифр — договор с барьером CLAUSE_REF, который отличает "
            "дату от номера пункта именно 4-значной группой",
}


def _strip_factory_keys(entity_types):
    """Убирает ключи, которые добавляет фабрика (sets/measure/sets_why) и
    которых детектор не читает, — для сравнения с дофабричным снимком."""
    out = {}
    for t, spec in entity_types.items():
        if isinstance(spec, dict):
            spec = {k: v for k, v in spec.items()
                    if k not in ("sets", "sets_why", "measure")}
        out[t] = spec
    return out


class TestAssembly:

    def test_on_disk_config_matches_assembly(self):
        """entity_types.yaml — собранный артефакт: ручная правка не живёт."""
        assert AT.assemble() == open(CONFIG, encoding="utf-8").read()

    def test_migrated_types_parse_identically_to_pre_factory_snapshot(self):
        """Ядро плана перевода: для каждого типа дофабричного снимка разобранная
        запись собранного конфига РАВНА снимку (кроме добавленных фабрикой
        ключей). Порядок записей тоже равен — он влияет на порядок детекции."""
        with open(SNAPSHOT, encoding="utf-8") as f:
            before = yaml.safe_load(f)["entity_types"]
        with open(CONFIG, encoding="utf-8") as f:
            after = yaml.safe_load(f)["entity_types"]
        after_stripped = _strip_factory_keys(after)
        for t, spec in before.items():
            assert t in after_stripped, f"тип {t} пропал при переводе"
            if t in SANCTIONED_CHANGES:
                assert after_stripped[t] != spec, (
                    f"тип {t} числится в SANCTIONED_CHANGES, но не отличается "
                    f"от снимка — запись протухла, уберите её")
                continue
            assert after_stripped[t] == spec, (
                f"тип {t}: разобранная запись изменилась при переводе — "
                f"поведение перестало быть тем же")
        before_names = list(before)
        after_names = [t for t in after if t in before]
        assert after_names == before_names, "порядок дофабричных типов сдвинулся"

    def test_arbitration_order_preserved_for_base_types(self):
        """Итоговый arbitration_order.regex, суженный до дофабричных типов,
        дословно равен порядку src/tokenizer.py до фабрики."""
        with open(CONFIG, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        order = cfg.get("arbitration_order", {})
        regex_base = [t for t in order.get("regex", []) if t in AT.BASE_REGEX_ORDER]
        assert regex_base == AT.BASE_REGEX_ORDER
        assert order.get("ner") == AT.BASE_NER_ORDER

    def test_tokenizer_priority_lists_come_from_assembled_config(self):
        """Шаг 1 плана: рабочие списки tokenizer'а == arbitration_order
        собранного конфига, и для дофабричных типов == историческим умолчаниям
        (двойное равенство = поведение не сдвинулось)."""
        sys.path.insert(0, SRC)
        import tokenizer as TOK
        with open(CONFIG, encoding="utf-8") as f:
            order = yaml.safe_load(f)["arbitration_order"]
        assert TOK._REGEX_PRIORITY == order["regex"]
        assert TOK._NER_PRIORITY == order["ner"]
        base_only = [t for t in TOK._REGEX_PRIORITY
                     if t in TOK._REGEX_PRIORITY_DEFAULT]
        assert base_only == TOK._REGEX_PRIORITY_DEFAULT
        assert [t for t in TOK._NER_PRIORITY
                if t in TOK._NER_PRIORITY_DEFAULT] == TOK._NER_PRIORITY_DEFAULT

    def test_type_policy_sets_match_config(self):
        """Шаг 2 плана: составы наборов, собранные из конфига, по СОСТАВУ равны
        историческим кортежам (порядок другой — конфигный, наборы потребляются
        как множества)."""
        sys.path.insert(0, SRC)
        import type_policy as TP
        assert set(TP.PERSONAL) == set(TP.PERSONAL_DEFAULT)
        # >=: новые фабричные типы вправе входить в широкие наборы; сузиться
        # против исторического состава набор не может
        assert set(TP.PERSONAL_REQUISITES) >= set(TP.PERSONAL_REQUISITES_DEFAULT)
        assert set(TP.WITH_MONEY) >= set(TP.WITH_MONEY_DEFAULT)
        assert TP._sets_from_config() is not None, (
            "конфиг без sets — наборы молча живут на умолчаниях")

    def test_digit_run_class_reproduces_historical_patterns(self):
        """Класс digit_run обязан выдавать ПОСИМВОЛЬНО те же паттерны, что
        стояли в конфиге до фабрики (иначе тип переводится raw, а не паттерн
        подгоняется). Эталон — САМ дофабричный снимок, не литерал в тесте:
        первая версия теста несла невидимую опечатку (два пробела вместо
        «пробел + NBSP») и была зелёной на двух одинаково неверных строках."""
        with open(SNAPSHOT, encoding="utf-8") as f:
            before = yaml.safe_load(f)["entity_types"]
        cases = [
            ({"digits": 10}, before["INN"]["pattern"]),
            ({"digits": 12}, before["INN_PERSON"]["pattern"]),
            ({"digits": 13, "optional_extra": 2}, before["OGRN"]["pattern"]),
            ({"digits": 9}, before["KPP"]["patterns"][0]["pattern"]),
            ({"digits": 20}, before["BANK_ACCOUNT"]["pattern"]),
            ({"digits": 9, "prefix": "04"}, before["BIK"]["pattern"]),
        ]
        for params, expected in cases:
            got = AT.compile_form("PROBE", {"class": "digit_run", "params": params})
            assert got == expected, (params, got, expected)


# --------------------------------------------------------------------------- #
#  3. Валидатор
# --------------------------------------------------------------------------- #

def _valid_typedef():
    return {
        "type": "PROBE",
        "title": "Проба",
        "status": "active",
        "detect": {
            "form": {"class": "digit_run", "params": {"digits": 7}},
            "token_prefix": "PROBE",
            "rank": 155,
            "sets": ["with_money"],
            "measure": {"leak_class": "numeric"},
        },
        "generator": {
            "provenance": "тест",
            "examples": ["1234567", "7654321", "1111111", "2222222", "3333333"],
            "negatives": [
                {"kind": "a", "text": "случай а", "why": "потому"},
                {"kind": "b", "text": "случай б", "why": "потому"},
                {"kind": "c", "text": "случай в", "why": "потому"},
            ],
        },
    }


def _write_typedef(tmp_path, td):
    p = tmp_path / (td["type"] + ".yaml")
    p.write_text(yaml.safe_dump(td, allow_unicode=True), encoding="utf-8")
    return str(p)


class TestValidator:

    def _expect(self, td, fragment, tmp_path):
        path = _write_typedef(tmp_path, td)
        with pytest.raises(SystemExit) as exc:
            AT.validate_typedef(td, path)
        assert fragment in str(exc.value), str(exc.value)

    def test_valid_typedef_passes(self, tmp_path):
        td = _valid_typedef()
        AT.validate_typedef(td, _write_typedef(tmp_path, td))

    def test_no_negatives_fails_with_neg_lesson(self, tmp_path):
        td = _valid_typedef()
        td["generator"]["negatives"] = []
        self._expect(td, "урок NEG", tmp_path)

    def test_one_kind_of_negatives_is_not_enough(self, tmp_path):
        td = _valid_typedef()
        for n in td["generator"]["negatives"]:
            n["kind"] = "same"
        self._expect(td, "видов негативов 1", tmp_path)

    def test_few_examples_fail(self, tmp_path):
        td = _valid_typedef()
        td["generator"]["examples"] = ["1", "2"]
        self._expect(td, "нужно >=5", tmp_path)

    def test_missing_rank_names_the_consequence(self, tmp_path):
        td = _valid_typedef()
        del td["detect"]["rank"]
        self._expect(td, "молча проигрывает каждое пересечение", tmp_path)

    def test_missing_sets_fails(self, tmp_path):
        td = _valid_typedef()
        del td["detect"]["sets"]
        self._expect(td, "в какие наборы", tmp_path)

    def test_empty_sets_require_why(self, tmp_path):
        td = _valid_typedef()
        td["detect"]["sets"] = []
        self._expect(td, "sets_why", tmp_path)

    def test_missing_measure_names_birthdate_precedent(self, tmp_path):
        td = _valid_typedef()
        del td["detect"]["measure"]
        self._expect(td, "BIRTHDATE", tmp_path)

    def test_unknown_detect_key_fails(self, tmp_path):
        td = _valid_typedef()
        td["detect"]["ancor"] = "(?i)проба"
        self._expect(td, "ancor", tmp_path)

    def test_corpus_only_with_detect_is_rejected(self, tmp_path):
        td = _valid_typedef()
        td["status"] = "corpus_only"
        self._expect(td, "corpus_only", tmp_path)

    def test_legacy_generator_is_closed_list(self, tmp_path):
        td = _valid_typedef()
        td["generator"] = "legacy"
        self._expect(td, "legacy", tmp_path)

    def test_rank_collision_is_an_error(self):
        td = _valid_typedef()
        td["detect"]["rank"] = 200   # ранг OGRN
        with pytest.raises(SystemExit) as exc:
            AT.build_arbitration({"PROBE": td})
        assert "уже занят" in str(exc.value)

    def test_moved_rank_of_base_type_is_an_error(self):
        """Перевод не двигает место в арбитраже молча."""
        td = _valid_typedef()
        td["type"] = "INN"
        td["detect"]["rank"] = 105   # исторический — 300
        with pytest.raises(SystemExit) as exc:
            AT.build_arbitration({"INN": td})
        assert "исторического" in str(exc.value)
