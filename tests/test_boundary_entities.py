"""Фикс B3 — сущности, РАЗОРВАННЫЕ границей соседних сегментов.

detect_regex/detect_ner работают посегментно, поэтому значение, чей текст
разложен на хвост одного сегмента и голову следующего (перенос абзаца/строки
или две соседние ячейки таблицы), раньше не находил ни один из них и утекал в
открытом виде (см. BREAKING_REPORT.md, находка B3).

`tokenizer._detect_boundary_entities` строит по каждой паре соседних сегментов
граничное окно `хвост A + разделитель + голова B` и повторно гоняет по нему те
же публичные детекторы. Разделитель — тот же, что в финальной сборке текста
('\\n'), КРОМЕ соседних ячеек одной строки таблицы: там окно склеивается БЕЗ
разделителя (в build_plain_text между ячейками ' | ' — но этот литерал физически
рвёт паттерны, а порванный реквизит обязан реконструироваться).

Репродукция самого дефекта (xfail(strict)) живёт отдельно —
tests/test_breaking_leaks.py::TestEntitySplitAcrossSegmentBoundary. Здесь —
прямые тесты ПРОХОЖДЕНИЯ через полный tokenize() после фикса.
"""
import pytest

from extractor import extract
from regex_detector import detect_regex
from ner_detector import detect_ner
from tokenizer import tokenize, build_plain_text
from session_store import save_session
from detokenizer import detokenize
from models import SourceDocument, TextSegment


def _pipeline(doc, config_path):
    """Полный конвейер детекции + токенизации; возвращает (анонимизированный текст, result)."""
    entities = detect_regex(doc, config_path) + detect_ner(doc, config_path)
    return tokenize(doc, entities, config_path)


def _txt_doc(tmp_path, content, filename="d.txt"):
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return extract(str(p))


def _seg(seg_id, text, source_type="txt_line", metadata=None):
    if metadata is None:
        metadata = {"line_index": 0}
    return TextSegment(id=seg_id, text=text, source_type=source_type, metadata=metadata)


# --------------------------------------------------------------------------- #
# Телефон, разорванный переносом строки между двумя соседними txt_line
# --------------------------------------------------------------------------- #
class TestPhoneSplitAcrossTxtLines:
    def test_phone_across_two_txt_lines_is_not_leaked(self, tmp_path, config_path):
        """B3: '+7 (495)' в конце одной строки, '123-45-67' — в начале следующей.
        Ни одна из половин раньше не находилась, номер утекал. После фикса — обе
        исчезают из текста."""
        doc = _txt_doc(tmp_path, "Звоните +7 (495)\n123-45-67 в будни")
        anon, _ = _pipeline(doc, config_path)
        assert "123-45-67" not in anon, "хвост телефона утёк"
        assert "+7 (495)" not in anon, "код телефона утёк"

    def test_both_halves_get_their_own_token(self, tmp_path, config_path):
        """СМЕНА КОНТРАКТА (фикс порчи B3, Вариант А).

        Старое ожидание: обе половины получают ОДИН общий токен ([PHONE_1] +
        [PHONE_1]). Оно порождало тихую порчу данных — session_store дедуплицирует
        по токену, поэтому при decrypt оба вхождения восстанавливались текстом
        первой половины, а хвост номера терялся (round-trip ниже это фиксирует).

        Новое ожидание: у каждой половины свой original_text -> свой токен
        ([PHONE_1] для '+7 (495)', [PHONE_2] для '123-45-67'), каждый ровно один
        раз. Ключевое требование B3 — обе половины токенизированы, утечки нет —
        сохраняется (см. test_..._is_not_leaked)."""
        doc = _txt_doc(tmp_path, "Звоните +7 (495)\n123-45-67 в будни")
        anon, result = _pipeline(doc, config_path)
        assert anon.count("[PHONE_1]") == 1, f"ожидалась одна подстановка [PHONE_1]: {anon!r}"
        assert anon.count("[PHONE_2]") == 1, f"половины должны получить РАЗНЫЕ токены: {anon!r}"
        phone_tokens = {e.token for e in result if e.entity_type == "PHONE"}
        assert phone_tokens == {"[PHONE_1]", "[PHONE_2]"}

    def test_split_halves_land_in_the_correct_segments(self, tmp_path, config_path):
        """Половины возвращаются как два Entity в РАЗНЫХ сегментах с реальными
        оффсетами внутри своих segment.text (инвариант original_text == срез)."""
        doc = _txt_doc(tmp_path, "Звоните +7 (495)\n123-45-67 в будни")
        _, result = _pipeline(doc, config_path)
        phones = sorted((e for e in result if e.entity_type == "PHONE"), key=lambda e: e.segment_id)
        assert [e.segment_id for e in phones] == ["l0", "l1"]
        seg_by_id = {s.id: s for s in doc.segments}
        for e in phones:
            assert seg_by_id[e.segment_id].text[e.start:e.end] == e.original_text


# --------------------------------------------------------------------------- #
# Телефон, ЦЕЛИКОМ помещающийся в один сегмент — обычная детекция ловит его,
# граничное окно НЕ должно создавать дубликат/второй токен
# --------------------------------------------------------------------------- #
class TestEntityWhollyInsideOneSegment:
    def test_phone_inside_single_multiline_segment_single_token(self, config_path):
        """Телефон с внутренним переносом внутри ОДНОГО многострочного сегмента —
        обычный detect_regex матчит его через \\s как одну сущность. Граница здесь
        не при чём: один сегмент -> граничных пар нет, второй токен не появляется."""
        doc = SourceDocument(
            segments=[_seg("l0", "Тел +7 (495)\n123-45-67 ок")],
            source_format="txt", source_path="d.txt",
        )
        anon, result = _pipeline(doc, config_path)
        assert anon.count("[PHONE_1]") == 1
        assert "[PHONE_2]" not in anon
        assert sum(e.entity_type == "PHONE" for e in result) == 1

    def test_phone_wholly_in_later_segment_not_duplicated_by_window(self, config_path):
        """Многосегментный документ, телефон целиком во втором сегменте. Граничное
        окно видит его в голове B, но он НЕ пересекает стык -> окно его отбрасывает
        как дубликат обычной детекции. Ровно один токен, не два."""
        doc = SourceDocument(
            segments=[
                _seg("l0", "Контакты организации"),
                _seg("l1", "тел +7 (495) 123-45-67 конец"),
            ],
            source_format="txt", source_path="d.txt",
        )
        anon, result = _pipeline(doc, config_path)
        assert "123-45-67" not in anon
        assert anon.count("[PHONE_1]") == 1
        assert "[PHONE_2]" not in anon
        assert sum(e.entity_type == "PHONE" for e in result) == 1


# --------------------------------------------------------------------------- #
# Организация, разорванная между двумя docx_paragraph
# --------------------------------------------------------------------------- #
class TestOrgSplitAcrossParagraphs:
    @pytest.mark.xfail(strict=True, reason=(
        "Этап A (structure-first ORG): B3-реконструкция ORG через границу абзаца "
        "опиралась на Natasha-ORG в граничном окне. Natasha-ORG отключена, структурный "
        "движок посегментный — разрыв «ООО | Ромашка» не сшивается. Реальная дыра "
        "покрытия (частичная утечка «Ромашка»), одна из двух зеркальных с "
        "test_breaking_leaks::test_org_split_across_lines. Cross-segment ORG для "
        "structure-first ORG — задача будущего этапа. Находка этапа A.")
    )
    def test_org_distinctive_part_no_longer_leaks(self, tmp_path, config_path, docx_factory):
        """B3: 'ООО' в одном абзаце, отличительная 'Ромашка' — в следующем. Раньше
        'Ромашка' утекала (PIN в test_breaking_leaks). NER в граничном окне видит
        единый ORG-спан через перенос -> обе части токенизируются, утечки нет.

        Примечание: 'ООО' самостоятельно детектится как ORG в своём абзаце, поэтому
        A-половина проигрывает обычному совпадению при разрешении пересечений — это
        документированный «резкий» случай (Шаг 5): половины МОГУТ получить разные
        токены, но ключевое требование B3 — отсутствие утечки — выполнено."""
        path = docx_factory(
            "org.docx",
            paragraphs_before=["Договор с ООО", "Ромашка на поставку"],
        )
        doc = extract(path)
        anon, result = _pipeline(doc, config_path)
        assert "Ромашка" not in anon, "отличительное имя организации утекло"
        assert "ООО" not in anon
        assert any(e.entity_type == "ORG" and e.original_text == "Ромашка" for e in result)


# --------------------------------------------------------------------------- #
# Реквизит, разорванный между соседними ячейками одной строки таблицы
# --------------------------------------------------------------------------- #
class TestRequisiteSplitAcrossTableCells:
    def test_inn_split_across_adjacent_cells_gets_distinct_tokens(self, tmp_path, config_path, docx_factory):
        """СМЕНА КОНТРАКТА (фикс порчи B3, Вариант А).

        B3: ИНН 7707083893 разложен по двум соседним ячейкам строки ('770708' |
        '3893'). Окно склеивает ячейки БЕЗ разделителя -> '7707083893' матчится,
        проходит чек-сумму, обе половины токенизируются (утечки нет).

        Старое ожидание: обе половины -> один общий токен [INN_1] (и порча при
        восстановлении). Новое ожидание: у половин разный original_text -> разные
        токены [INN_1] ('770708') и [INN_2] ('3893'), каждый ровно один раз."""
        path = docx_factory("inn.docx", table_rows=[["770708", "3893"]])
        doc = extract(path)
        anon, result = _pipeline(doc, config_path)
        assert "770708" not in anon and "3893" not in anon, "часть ИНН утекла"
        assert anon.count("[INN_1]") == 1
        assert anon.count("[INN_2]") == 1
        inn_tokens = {e.token for e in result if e.entity_type == "INN"}
        assert inn_tokens == {"[INN_1]", "[INN_2]"}

    def test_phone_split_across_adjacent_cells_gets_distinct_tokens(self, tmp_path, config_path, docx_factory):
        """СМЕНА КОНТРАКТА (фикс порчи B3, Вариант А).

        Тот же класс, что phone-across-cells из BREAKING_REPORT (B3): код и остаток
        номера в соседних ячейках. Склейка без разделителя реконструирует номер,
        обе половины токенизируются (утечки нет).

        Старое ожидание: общий токен [PHONE_1] на обе ячейки. Новое: у половин
        разный original_text -> разные токены [PHONE_1] ('+7 (495)') и [PHONE_2]
        ('123-45-67')."""
        path = docx_factory("phone.docx", table_rows=[["+7 (495)", "123-45-67"]])
        doc = extract(path)
        anon, result = _pipeline(doc, config_path)
        assert "123-45-67" not in anon, "хвост телефона утёк через границу ячейки"
        phone_tokens = {e.token for e in result if e.entity_type == "PHONE"}
        assert phone_tokens == {"[PHONE_1]", "[PHONE_2]"}
        assert anon.count("[PHONE_1]") == 1
        assert anon.count("[PHONE_2]") == 1


# --------------------------------------------------------------------------- #
# ЭТАП A6 — реквизит, разорванный границей сегментов-СТРОК ('\n' в стыке окна).
# Regex-проход по окну через '\n' не матчится осознанно (A2-NEWLINE-CROSS),
# сборку делает multispan.collect_seam_entities: цепь цифровых групп под якорем,
# сумма цифр точно равна длине ОДНОГО значения, КС где определена, и ни одна
# сторона разрыва не является целым значением сама по себе.
# Значения синтетические, КС посчитаны при написании теста:
#   ОГРН-13 1122334455660 валиден; 1122334455661 — КС сломана;
#   ОГРНИП-15 112233445566071 валиден И его префикс-13 валиден (страж частей);
#   ИНН-12 771234567859 валиден.
# --------------------------------------------------------------------------- #
class TestRequisiteSplitAcrossTxtLines:
    @pytest.mark.parametrize("lines, etype, halves", [
        (["р/с 407028106", "00849653120 в банке"], "BANK_ACCOUNT",
         ("407028106", "00849653120")),
        (["ОГРН 34", "5305901325030, далее"], "OGRN", ("34", "5305901325030")),
        (["ИНН 7712345", "67859 работника"], "INN_PERSON", ("7712345", "67859")),
        # половина B по контракту B3 начинается с оффсета 0 сегмента — ведущий
        # дефис входит в неё («-789 12»), маска закрывает его вместе с цифрами
        (["СНИЛС 123-456", "-789 12"], "SNILS", ("123-456", "-789 12")),
    ], ids=["account", "ogrn", "inn12", "snils"])
    def test_torn_requisite_is_assembled_and_masked(self, tmp_path, config_path,
                                                    lines, etype, halves):
        """Значение, рваное переносом строки (в .txt строка = сегмент), собирается
        через стык окна и обе половины уходят под маску — это остаток A5
        (A5-NL-CROSS-RESIDUE), класс ACCOUNT/OGRN на живом гейте."""
        doc = _txt_doc(tmp_path, "\n".join(lines))
        anon, result = _pipeline(doc, config_path)
        for half in halves:
            assert half not in anon, f"половина {half!r} утекла: {anon!r}"
        got = sorted(e.original_text for e in result if e.entity_type == etype)
        assert got == sorted(halves), f"ожидались обе половины {halves}: {got}"

    def test_halves_land_in_their_segments_with_true_offsets(self, tmp_path, config_path):
        """Контракт B3: половины — два Entity в РАЗНЫХ сегментах, original_text ==
        срез segment.text (фундамент обратимости)."""
        doc = _txt_doc(tmp_path, "р/с 407028106\n00849653120 в банке")
        _, result = _pipeline(doc, config_path)
        accs = sorted((e for e in result if e.entity_type == "BANK_ACCOUNT"),
                      key=lambda e: e.segment_id)
        assert [e.segment_id for e in accs] == ["l0", "l1"]
        seg_by_id = {s.id: s for s in doc.segments}
        for e in accs:
            assert seg_by_id[e.segment_id].text[e.start:e.end] == e.original_text

    def test_torn_pair_round_trips_byte_exact(self, tmp_path, config_path):
        """Обратимость: tokenize -> save_session -> detokenize возвращает плоский
        текст посимвольно (требование 2 этапа A6)."""
        doc = _txt_doc(tmp_path, "р/с 407028106\n00849653120, ОГРН 34\n5305901325030 конец")
        anon, result = _pipeline(doc, config_path)
        session_id = save_session(result, storage_dir=str(tmp_path / "s"))
        restored, unresolved = detokenize(anon, session_id, storage_dir=str(tmp_path / "s"))
        assert unresolved == []
        assert restored == build_plain_text(doc)


class TestSeamAssemblyGuards:
    """Направление ошибки: лучше не найти, чем захватить лишнее. Каждый страж —
    отдельный отрицательный тест."""

    def test_broken_checksum_is_not_stitched(self, tmp_path, config_path):
        """КС ОГРН сломана -> цепь не собирается, обе строки нетронуты (честный
        промах вместо кривого попадания)."""
        doc = _txt_doc(tmp_path, "ОГРН 11\n22334455661")
        anon, result = _pipeline(doc, config_path)
        assert anon == "ОГРН 11\n22334455661"

    def test_no_anchor_no_assembly(self, tmp_path, config_path):
        """Без якоря сборка запрещена ВСЕМ типам — даже тем, кого посегментная
        детекция берёт по одной КС (ИНН): сборка рискованнее, требования строже."""
        doc = _txt_doc(tmp_path, "407028106\n00849653120")
        anon, result = _pipeline(doc, config_path)
        assert result == []
        assert anon == "407028106\n00849653120"

    def test_complete_value_on_one_side_blocks_stitching(self, tmp_path, config_path):
        """Страж частей (урок A2-NEWLINE-CROSS): целый валидный ОГРН-13 плюс «71»
        на следующей строке дали бы валидный ОГРНИП-15 — но через настоящую
        границу не сшивается то, что цело уже по одну её сторону. Целое значение
        маскируется само по себе, хвост «71» не трогается."""
        doc = _txt_doc(tmp_path, "ОГРН 1122334455660\n71 экз.")
        anon, result = _pipeline(doc, config_path)
        assert anon == "ОГРН [OGRN_1]\n71 экз."

    def test_two_values_in_column_are_not_stitched(self, tmp_path, config_path):
        """Зеркало длины: два счёта в столбик дают 40 цифр -> цепь отвергается
        ЦЕЛИКОМ, никаких под-окон. Первый счёт (под якорем) маскирует штатный
        паттерн; второй без якоря на своей строке не берётся — правило этапа 4,
        не дефект сборки."""
        doc = _txt_doc(tmp_path, "р/с 40702810349511286477\n40702810600849653120")
        anon, result = _pipeline(doc, config_path)
        accs = [e for e in result if e.entity_type == "BANK_ACCOUNT"]
        assert len(accs) == 1 and accs[0].segment_id == "l0"
        assert "40702810349511286477" not in anon

    def test_birthdate_torn_across_lines_is_assembled(self, tmp_path, config_path):
        """BIRTHDATE через стык — теми же _BDATE-паттернами, что внутри сегмента
        (якорь рождения в самом регэкспе). Ровно класс четырёх документов A2."""
        doc = _txt_doc(tmp_path, "Дата рождения: 01.02.\n1980, г. Москва")
        anon, result = _pipeline(doc, config_path)
        assert "1980" not in anon
        assert "01.02." not in anon
        assert any(e.entity_type == "BIRTHDATE" for e in result)


# --------------------------------------------------------------------------- #
# Обычный стык без сущностей — окно НЕ должно фабриковать ложные Entity
# --------------------------------------------------------------------------- #
class TestBoundaryWindowNoFalsePositives:
    def test_plain_prose_boundary_produces_no_entities(self, tmp_path, config_path):
        """На стыке обычного текста без ПДн граничное окно не должно порождать ни
        одной сущности (иначе фикс сам бы стал источником шума)."""
        doc = _txt_doc(tmp_path, "Первая строка обычного текста\nвторая строка тоже обычная")
        anon, result = _pipeline(doc, config_path)
        assert result == []
        assert "[" not in anon
        assert anon == "Первая строка обычного текста\nвторая строка тоже обычная"

    def test_boundary_does_not_stitch_across_pipe_separated_prose(self, config_path):
        """Контроль разделителя: две ячейки РАЗНЫХ строк таблицы (в сборке между
        ними '\\n', а не склейка) — числа, случайно оказавшиеся рядом, не должны
        сшиваться в реквизит через границу строк таблицы."""
        segs = [
            _seg("t0_r0_c0", "770708", "docx_table_cell",
                 {"table_index": 0, "row_index": 0, "col_index": 0}),
            _seg("t0_r1_c0", "3893", "docx_table_cell",
                 {"table_index": 0, "row_index": 1, "col_index": 0}),
        ]
        doc = SourceDocument(segments=segs, source_format="docx", source_path="d.docx")
        anon, result = _pipeline(doc, config_path)
        # разные строки -> разделитель '\n' -> ИНН через перенос не матчится (класс
        # [ ] не включает '\n') -> ложного реквизита нет
        assert not any(e.entity_type == "INN" for e in result)


# --------------------------------------------------------------------------- #
# Round-trip encrypt -> decrypt для граничной пары.
# Именно ОТСУТСТВИЕ этого теста пропустило порчу данных B3: посегментная
# токенизация проверялась, а полный цикл tokenize -> save_session -> detokenize
# для разорванных границей сущностей — никогда. С общим токеном на пару
# session_store дедуплицировал по токену, оставлял значение первой половины, и
# decrypt подставлял его в ОБА вхождения -> хвост уничтожался (сценарий 1) либо
# невинное число молча заменялось (сценарий 2). Вариант А (раздельные токены)
# делает восстановление посимвольно точным.
# --------------------------------------------------------------------------- #
class TestBoundaryPairRoundTrip:
    def _round_trip(self, doc, config_path, storage_dir):
        """tokenize -> save_session -> detokenize; возвращает (restored, unresolved, anon)."""
        anon, result = _pipeline(doc, config_path)
        session_id = save_session(result, storage_dir=str(storage_dir))
        restored, unresolved = detokenize(anon, session_id, storage_dir=str(storage_dir))
        return restored, unresolved, anon

    def test_scenario1_phone_across_txt_lines_restores_exactly(self, tmp_path, config_path):
        """Сценарий 1 (реальная сущность): телефон, разорванный переносом строки.
        До фикса decrypt возвращал '... +7 (495)\\n+7 (495) ...' — хвост '123-45-67'
        уничтожен. После фикса восстановление посимвольно совпадает с исходным
        плоским текстом, обе половины на месте."""
        doc = _txt_doc(tmp_path, "Контактный телефон +7 (495)\n123-45-67 указан в договоре")
        restored, unresolved, anon = self._round_trip(doc, config_path, tmp_path / "sessions")
        assert unresolved == [], f"остались неразрешённые токены: {unresolved}"
        assert restored == build_plain_text(doc), (
            f"round-trip не совпал посимвольно:\n  ждали:   {build_plain_text(doc)!r}\n"
            f"  получили: {restored!r}"
        )
        # явная фиксация: обе половины восстановлены своими значениями, хвост цел
        assert "+7 (495)" in restored and "123-45-67" in restored

    def test_scenario2_false_positive_preserves_innocent_number(self, tmp_path, config_path):
        """Сценарий 2 (ложное срабатывание): соседние ячейки строки таблицы
        'Приложение 1' | '23456789 экз.'. Окно склеивает их в '123456789' -> KPP.
        До фикса восстановление давало '... 1 | 1 экз.' — число 23456789 молча
        заменено. После фикса: раздельные токены '1'->[KPP_1], '23456789'->[KPP_2],
        восстановление посимвольно точно, невинное число цело."""
        segs = [
            _seg("t0_r0_c0", "Приложение 1", "docx_table_cell",
                 {"table_index": 0, "row_index": 0, "col_index": 0}),
            _seg("t0_r0_c1", "23456789 экз.", "docx_table_cell",
                 {"table_index": 0, "row_index": 0, "col_index": 1}),
        ]
        doc = SourceDocument(segments=segs, source_format="docx", source_path="d.docx")
        restored, unresolved, anon = self._round_trip(doc, config_path, tmp_path / "sessions")
        assert unresolved == [], f"остались неразрешённые токены: {unresolved}"
        assert restored == build_plain_text(doc), (
            f"round-trip не совпал посимвольно:\n  ждали:   {build_plain_text(doc)!r}\n"
            f"  получили: {restored!r}"
        )
        # ключевое: невинное число не потеряно при восстановлении
        assert "23456789" in restored, "невинное число уничтожено восстановлением"
