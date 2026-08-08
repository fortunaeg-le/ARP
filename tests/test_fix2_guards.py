# -*- coding: utf-8 -*-
"""Стражи этапа FIX-2 — три механизма, каждый заведён по НАЙДЕННОЙ дыре.

1. ЗАМОРОЗКА КОРПУСА ВИДИТ ДОПОЛНЕНИЕ. `MANIFEST.sha256` — белый список: имена
   файлов он читает из самого себя, поэтому видел «изменился» и «исчез», но не
   «ПОЯВИЛСЯ». Запрет 2 корневого `CLAUDE.md` говорит «не редактировать, НЕ
   ДОПОЛНЯТЬ, не удалять» — держались два слова из трёх.
2. ЯКОРЬ ЧЕРЕЗ ГРАНИЦУ ЯЧЕЙКИ (`DOG-ANCHOR-SPLIT`). «БИК» в одной ячейке
   строки, число — в соседней: посегментная детекция якоря не видит, а
   граничное окно отдаёт только спаны, ПЕРЕСЁКШИЕ стык. Этап NEG потерял на
   этом единственный настоящий БИК реального договора.
3. ОКНО АНТИ-ЯКОРЯ — СВОЁ У КАЖДОГО ТИПА. Общая константа не давала расширить
   окно там, где нужно, не тронув PASSPORT.

Тесты — стражи, а не переизложение кода: каждый ломается, если механизм снимут.
"""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, os.path.join(_ROOT, "tests", "corpus"))

from models import SourceDocument, TextSegment  # noqa: E402

CONFIG = os.path.join(_ROOT, "entity_types.yaml")


# --------------------------------------------------------------------------- 1
class TestFrozenCorpusSeesAddition:
    def test_manifest_is_clean_right_now(self):
        import gate
        ok, problems, n = gate.check_manifest()
        assert ok, problems[:5]
        assert n > 600, "манифест внезапно похудел — это само по себе находка"

    def test_extra_file_reddens_the_check(self, tmp_path, monkeypatch):
        """Проба на СВОЁМ дереве, а не на настоящем корпусе.

        Первая редакция теста клала файл прямо в `tests/corpus/docs/` и
        убирала его в `finally`. Это ложилось дважды: во-первых, тест сам
        трогал бы замороженный корпус (запрет 2), во-вторых — прогон идёт
        параллельно (`xdist -n auto`), и соседний воркер видел пробный файл
        как настоящее нарушение. Здесь подменяются `HERE`/`MANIFEST`, поэтому
        проверяется ровно механизм и ничего общего не трогается."""
        import hashlib
        import gate

        (tmp_path / "docs").mkdir()
        doc = tmp_path / "docs" / "a.txt"
        doc.write_text("документ корпуса", encoding="utf-8")
        digest = hashlib.sha256(doc.read_bytes()).hexdigest()
        man = tmp_path / "MANIFEST.sha256"
        man.write_text("%s  docs/a.txt\n" % digest, encoding="utf-8")

        monkeypatch.setattr(gate, "HERE", str(tmp_path))
        monkeypatch.setattr(gate, "MANIFEST", str(man))

        ok, problems, n = gate.check_manifest()
        assert ok and n == 1, problems

        (tmp_path / "docs" / "лишний.txt").write_text("подложен", encoding="utf-8")
        ok, problems, _ = gate.check_manifest()
        assert not ok, "манифест не заметил ДОПОЛНЕНИЯ — дыра вернулась"
        assert any("ПОЯВИЛСЯ" in p for p in problems), problems

        (tmp_path / "docs" / "лишний.txt").unlink()
        ok, problems, _ = gate.check_manifest()
        assert ok, problems

        doc.write_text("подменён", encoding="utf-8")
        ok, problems, _ = gate.check_manifest()
        assert not ok and any("sha256" in p for p in problems), problems


# --------------------------------------------------------------------------- 2
def _cells(*texts):
    """Строка таблицы: ячейки одной строки одной таблицы (пустой _boundary_sep)."""
    return SourceDocument(
        segments=[
            TextSegment(id="c%d" % i, text=t, source_type="docx_table_cell",
                        metadata={"table_index": 0, "row_index": 0,
                                  "cell_index": i})
            for i, t in enumerate(texts)
        ],
        source_format="docx", source_path="проба.docx",
    )


def _types(doc):
    from pipeline import run_detection
    from tokenizer import resolve_for_masking
    return [(e.entity_type, e.original_text)
            for e in resolve_for_masking(doc, run_detection(doc, CONFIG), CONFIG)]


class TestAnchorAcrossCellBoundary:
    def test_bik_is_found_when_the_anchor_sits_in_the_neighbouring_cell(self):
        """Ровно потерянный этапом NEG случай реального договора."""
        found = _types(_cells("БИК", "044525225"))
        assert ("BIK", "044525225") in found, found

    def test_the_same_number_without_the_anchor_is_not_masked(self):
        """690 ложных БИК не возвращаются: без слова «БИК» рядом — ничего.

        Если этот тест покраснеет, значит склейка соседних ячеек начала
        подтверждать что попало, и правка NEG обнулена."""
        found = _types(_cells("Инвентарный номер", "044525225"))
        assert not [t for t, _ in found if t == "BIK"], found

    def test_anchor_does_not_reach_across_a_structural_paragraph_break(self):
        """Граница различается ПО РАЗМЕТКЕ: два абзаца — не ячейки одной строки,
        и якорь через них не тянется (направление ошибки «лучше не найти»)."""
        doc = SourceDocument(
            segments=[
                TextSegment(id="p1", text="БИК", source_type="docx_paragraph",
                            metadata={}),
                TextSegment(id="p2", text="044525225",
                            source_type="docx_paragraph", metadata={}),
            ],
            source_format="docx", source_path="проба.docx",
        )
        assert not [t for t, _ in _types(doc) if t == "BIK"], _types(doc)

    def test_result_is_deterministic_across_repeated_runs(self):
        doc = _cells("БИК", "044525225")
        runs = [_types(doc) for _ in range(3)]
        assert runs[0] == runs[1] == runs[2]


# --------------------------------------------------------------------------- 3
class TestPerTypeAntiAnchorWindow:
    def test_passport_keeps_the_historical_window(self):
        """PASSPORT НЕ ТРОНУТ: его окно подобрано под «ТЕХНИЧЕСКИЙ паспорт»,
        и движение общей константы меняло бы поведение типа, к находке
        отношения не имеющего."""
        from regex_detector import _load_regex_types, _ANTI_ANCHOR_WINDOW
        windows = {s[0]: s[6] for s in _load_regex_types(CONFIG)}
        assert windows["PASSPORT"] == _ANTI_ANCHOR_WINDOW == 20

    def test_widened_types_are_named_and_only_those(self):
        from regex_detector import _load_regex_types, _ANTI_ANCHOR_WINDOW
        wide = {s[0] for s in _load_regex_types(CONFIG)
                if s[6] != _ANTI_ANCHOR_WINDOW}
        assert wide == {"PHONE", "PERCENT", "TERM"}, wide

    @pytest.mark.parametrize("text,etype", [
        ("Общество осуществляет деятельность в течение 11 лет", "TERM"),
        ("Отчётный период по Договору завершается не позднее 05.10.2024", "TERM"),
        ("Партия внесена в реестр под номером 89619720389", "PHONE"),
        ("Погрешность измерения прибора — не более 0,8 %", "PERCENT"),
    ])
    def test_qualifier_beyond_twenty_chars_now_disqualifies(self, text, etype):
        """Каждый случай — вид, который этап NEG не закрыл ИМЕННО из-за окна:
        уточнитель стоит дальше 20 знаков."""
        from regex_detector import detect_regex
        doc = SourceDocument(
            segments=[TextSegment(id="s", text=text, source_type="txt_line",
                                  metadata={})],
            source_format="txt", source_path="проба.txt")
        assert not [e for e in detect_regex(doc, CONFIG)
                    if e.entity_type == etype], text

    def test_real_value_of_the_same_type_still_found(self):
        """Полнота: расширенное окно не должно гасить настоящее значение."""
        from regex_detector import detect_regex
        for text, etype in [("Неустойка составляет 0,1 % за каждый день просрочки",
                             "PERCENT"),
                            ("Срок оплаты — в течение 5 банковских дней", "TERM"),
                            ("Телефон 89619720389", "PHONE")]:
            doc = SourceDocument(
                segments=[TextSegment(id="s", text=text, source_type="txt_line",
                                      metadata={})],
                source_format="txt", source_path="проба.txt")
            assert [e for e in detect_regex(doc, CONFIG)
                    if e.entity_type == etype], text
