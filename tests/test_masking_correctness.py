# -*- coding: utf-8 -*-
"""ЗЕРКАЛА МЕТРИКИ masking_correctness (A/B/C) — доказательство, что метрика РАЗЛИЧАЕТ
корректную и кривую маскировку, а не всегда зелёная.

ЗАЧЕМ ЭТОТ ФАЙЛ СУЩЕСТВУЕТ
    Метрика корректности маскировки опасна ровно тем же, чем была опасна метрика
    утечки v1: её легко написать так, что она молча рапортует 100% при любом
    состоянии системы (не нашла, с чем сравнивать, — значит «нарушений нет»).
    Такая метрика хуже отсутствия метрики: она даёт ложную уверенность и её
    зелёный цвет попадает в гейт. Поэтому у КАЖДОГО из уровней A/B/C здесь есть
    ПАРА:
      положительное зеркало — канонический корректный случай -> метрика говорит OK;
      отрицательное зеркало — случай, про который ИЗВЕСТНО, что маскировка кривая
                              -> метрика говорит «нарушение».
    Пропадёт любое из двух — метрика перестанет быть измерением.

ЧТО ИМЕННО МЕРЯЕТСЯ (знаменатель!)
    Знаменатель — МАСКИ, поставленные системой, а НЕ gold. recall/leak отвечают на
    «сколько поймали»; masking_correctness — на «из пойманного, сколько спрятано
    корректно». Причина — асимметрия ошибок эксплуатации: пропуск человек на
    проверке поймает, кривую маскировку — нет (он ищет ЛИШНЕЕ, а не проверяет уже
    спрятанное).

ДВА ЯРУСА ЗЕРКАЛ
    1) юнит-зеркала на чистых функциях measure_lib (mc_check_a / mc_check_bc) —
       быстрые, задают ровно те граничные случаи, которые словами описаны в
       докстрингах (в т.ч. «маска ШИРЕ эталона — это НЕ нарушение B»);
    2) сквозные зеркала через НАСТОЯЩИЙ конвейер (extract -> detect -> tokenize ->
       detokenize) на временных документах и на замороженном корпусе — они ловят
       то, чего юнит-зеркало поймать не может: что метрика подключена к реальным
       координатам и реальному round-trip, а не к выдуманным.
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORPUS = os.path.join(_ROOT, "tests", "corpus")
sys.path.insert(0, _CORPUS)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import measure_lib as ML  # noqa: E402
import run_measurement as RM  # noqa: E402


# =========================================================================== #
#                 ЯРУС 1 — юнит-зеркала на чистых функциях                    #
# =========================================================================== #
class TestMirrorA:
    """A — round-trip: значение восстановилось decrypt'ом БАЙТ-В-БАЙТ."""

    PLAIN = "ИНН 7707083893 конец"

    def test_positive_clean_value_roundtrips(self):
        """Положительное зеркало: значение вернулось ровно -> A OK."""
        r = ML.mc_check_a(4, "7707083893", self.PLAIN, self.PLAIN)
        assert r["a_ok"] is True and r["a_channel_ok"] is True
        assert r["reason"] == "ok"

    def test_negative_corrupted_value_is_violation(self):
        """Отрицательное зеркало: decrypt вернул ДРУГИЕ цифры -> нарушение A.
        Одна цифра — уже брак: «примерно те же данные» требованием не является."""
        restored = "ИНН 7707083890 конец"
        r = ML.mc_check_a(4, "7707083893", restored, self.PLAIN)
        assert r["a_ok"] is False
        assert r["reason"] == "value_corrupted"

    def test_negative_length_mismatch_is_violation(self):
        """Отрицательное зеркало: длины restored и plain разошлись — координаты
        недействительны, доказать сохранность значения нечем -> нарушение
        КОНСЕРВАТИВНО (не «неприменимо», см. инвариант этапа 0b-fix)."""
        r = ML.mc_check_a(4, "7707083893", "мусор", self.PLAIN, aligned=False)
        assert r["a_ok"] is False and r["reason"] == "unaligned"

    def test_channel_whitespace_is_not_a_value_violation(self):
        """Разграничение, ради которого A считается двумя числами: в plain-сборке
        '\\n' внутри ячейки таблицы схлопнут в пробел, а detokenize возвращает
        исходный '\\n'. Значение при этом ЦЕЛО (a_ok), расходится только канал
        (a_channel_ok=False). Записывать это в нарушение A — подогнать метрику под
        артефакт сборки; молча игнорировать — потерять факт."""
        plain = "Счёт 407 028 108 конец"
        restored = "Счёт 407\n028 108 конец"
        r = ML.mc_check_a(5, "407\n028 108", restored, plain)
        assert r["a_ok"] is True
        assert r["a_channel_ok"] is False
        assert r["reason"] == "channel_ws_only"


class TestMirrorB:
    """B — границы: эталонный спан закрыт масками ЦЕЛИКОМ, не наполовину."""

    GOLD = [{"start": 10, "end": 30, "type": "ACCOUNT"}]

    def _bc(self, masks, i=0):
        return ML.mc_check_bc(masks[i], self.GOLD, masks)

    def test_positive_full_cover(self):
        masks = [{"start": 10, "end": 30, "gtype": "ACCOUNT"}]
        r = self._bc(masks)
        assert r["scored"] is True and r["b_ok"] is True

    def test_negative_half_masked_is_violation(self):
        """Отрицательное зеркало (главное для B): половина счёта под маской,
        половина открытым текстом. Это разом и утечка, и кривая маскировка —
        ровно случай «сущность разорвана переносом строки/границей ячейки»
        (SplitEntities). Метрика ОБЯЗАНА показать нарушение."""
        masks = [{"start": 10, "end": 20, "gtype": "ACCOUNT"}]
        assert self._bc(masks)["b_ok"] is False

    def test_two_adjacent_masks_cover_together(self):
        """Значение, закрытое ДВУМЯ соседними масками, спрятано полностью — это не
        брак. Поэтому B считается по ОБЪЕДИНЕНИЮ масок, накрывающих эталон, а не
        по одной маске: иначе честно закрытый разрыв считался бы нарушением."""
        masks = [{"start": 10, "end": 21, "gtype": "ACCOUNT"},
                 {"start": 21, "end": 30, "gtype": "ACCOUNT"}]
        assert self._bc(masks, 0)["b_ok"] is True
        assert self._bc(masks, 1)["b_ok"] is True

    def test_gap_between_masks_is_violation(self):
        """…но если между двумя масками остался открытый кусок — нарушение.
        Проверка того, что предыдущий тест не «зазеленил» B объединением вообще."""
        masks = [{"start": 10, "end": 18, "gtype": "ACCOUNT"},
                 {"start": 22, "end": 30, "gtype": "ACCOUNT"}]
        assert self._bc(masks, 0)["b_ok"] is False

    def test_wider_than_gold_is_NOT_a_B_violation(self):
        """PASSPORT-случай из STATE §4 (0c-B): спан маски ШИРЕ эталона,
        recall_exact 0%. По B это НЕ нарушение — B про «не половина», а не про
        «ровно». Точность спана меряет recall_exact, это другое требование, и
        смешивать их в одном числе нельзя: иначе B покраснеет от over-capture,
        который человек на проверке как раз увидит (лишнее замаскировано)."""
        masks = [{"start": 5, "end": 35, "gtype": "ACCOUNT"}]
        assert self._bc(masks)["b_ok"] is True

    def test_false_positive_mask_is_not_scored(self):
        """Маска, не легшая ни на одну эталонную сущность, — ложное срабатывание.
        Покрывать ей нечего, в знаменатель B/C она НЕ идёт (её меряет FP-метрика);
        иначе рост FP автоматически «ронял» бы корректность маскировки."""
        masks = [{"start": 100, "end": 110, "gtype": "ACCOUNT"}]
        r = self._bc(masks)
        assert r["scored"] is False and r["b_ok"] is None


class TestMirrorC:
    """C — тип: сущность спрятана под СВОИМ типом (мягкий уровень)."""

    GOLD = [{"start": 10, "end": 24, "type": "SNILS"}]

    def test_positive_same_type(self):
        masks = [{"start": 10, "end": 24, "gtype": "SNILS"}]
        r = ML.mc_check_bc(masks[0], self.GOLD, masks)
        assert r["c_ok"] is True

    def test_negative_snils_masked_as_kpp(self):
        """Отрицательное зеркало C: СНИЛС спрятан под маской KPP (известное
        нарушение из STATE §3 — детектора SNILS нет, значение подхватывает чужой
        regex). Границы при этом целы — то есть C нарушается НЕЗАВИСИМО от B, и
        метрика обязана их различать, а не сливать в одно число."""
        masks = [{"start": 10, "end": 24, "gtype": "KPP"}]
        r = ML.mc_check_bc(masks[0], self.GOLD, masks)
        assert r["b_ok"] is True, "границы целы — нарушение именно по типу"
        assert r["c_ok"] is False
        assert (r["gold_type"], r["mask_type"]) == ("SNILS", "KPP")


class TestRatesDenominator:
    """Знаменатель — маски системы, не gold. Тест пришпиливает это, потому что
    перепутать знаменатель = молча превратить masking_correctness в recall."""

    def test_rates_use_mask_denominators(self):
        recs = [
            # 4 маски; из них 3 легли на эталон
            {"gtype": "INN", "a_ok": True, "a_channel_ok": True, "scored": True,
             "b_ok": True, "c_ok": True},
            {"gtype": "INN", "a_ok": True, "a_channel_ok": True, "scored": True,
             "b_ok": False, "c_ok": True},
            {"gtype": "KPP", "a_ok": False, "a_channel_ok": False, "scored": True,
             "b_ok": True, "c_ok": False},
            {"gtype": "KPP", "a_ok": True, "a_channel_ok": True, "scored": False,
             "b_ok": None, "c_ok": None},
        ]
        agg = ML.mc_aggregate(recs)
        t = agg["total"]
        assert (t["n_masks"], t["n_scored"]) == (4, 3)
        a, b, c = ML.mc_rates(t)
        assert round(a, 2) == 75.00            # 3/4 — знаменатель ВСЕ маски
        assert round(b, 2) == round(200 / 3, 2)  # 2/3 — знаменатель легшие на эталон
        assert round(c, 2) == round(200 / 3, 2)


# =========================================================================== #
#            ЯРУС 2 — сквозные зеркала через НАСТОЯЩИЙ конвейер                #
# =========================================================================== #
def _run_text_doc(tmp_path, monkeypatch, text, entities):
    """Прогоняет РЕАЛЬНЫЙ конвейер (extract -> detect -> tokenize -> detokenize)
    по временному .txt и возвращает запись process_doc. Координаты сущностей в
    .txt совпадают с координатами PT-1 (файл читается как есть), поэтому gold
    для временного документа строится прямо по индексам в text.

    Корпус НЕ трогается: подменяется только каталог документов (RM.DOCS)."""
    doc_id = "mirror_tmp"
    (tmp_path / (doc_id + ".txt")).write_text(text, encoding="utf-8", newline="")
    monkeypatch.setattr(RM, "DOCS", str(tmp_path))
    d = {"doc_id": doc_id, "format": "txt", "source": "mirror",
         "entities": entities, "negatives": [], "ignore": []}
    return RM.process_doc(d)


def _find(text, value, etype):
    i = text.index(value)
    return {"start": i, "end": i + len(value), "type": etype, "text": value,
            "category": "base"}


def test_e2e_positive(tmp_path, monkeypatch):
    """ПОЛОЖИТЕЛЬНОЕ сквозное зеркало: чистый канонический ИНН на реальном
    конвейере — все три уровня зелёные. Если этот тест покраснеет, метрика красна
    по посторонней причине (конвейер/координаты), а не из-за системы."""
    text = "Договор.\nИНН организации 7707083893.\nКонец.\n"
    rec = _run_text_doc(tmp_path, monkeypatch, text,
                        [_find(text, "7707083893", "INN")])
    inn_masks = [m for m in rec["masks"] if m["gtype"] == "INN"]
    assert inn_masks, "система не замаскировала канонический ИНН — зеркало слепо"
    m = inn_masks[0]
    assert (m["a_ok"], m["scored"], m["b_ok"], m["c_ok"]) == (True, True, True, True)


def test_e2e_negative_A_token_literal_breaks_roundtrip(tmp_path, monkeypatch):
    """ОТРИЦАТЕЛЬНОЕ сквозное зеркало A на реальном конвейере.

    Документ, в котором УЖЕ есть литерал вида [INN_1], — detokenize подставит
    значение и в него тоже, восстановленный текст разъедется с оригиналом, и
    доказать сохранность замаскированного значения на его месте нечем. Метрика
    обязана показать нарушение A, а не отрапортовать 100%.

    Это не выдуманный случай, а именно тот класс, ради которого A существует:
    round-trip ломается НЕ там, где ломается детекция."""
    text = "Шаблон: [INN_1] подставить.\nИНН организации 7707083893.\n"
    rec = _run_text_doc(tmp_path, monkeypatch, text,
                        [_find(text, "7707083893", "INN")])
    assert rec["masks"], "нечего проверять — система ничего не замаскировала"
    assert not rec["roundtrip_ok"], "конвейер неожиданно устоял — зеркало неактуально"
    assert any(not m["a_ok"] for m in rec["masks"]), \
        "round-trip сломан, а метрика A этого не увидела — метрика фиктивна"


def test_e2e_negative_B_half_masked_value(tmp_path, monkeypatch):
    """ОТРИЦАТЕЛЬНОЕ сквозное зеркало B: эталонный спан размечен ШИРЕ того, что
    система в состоянии закрыть (значение + приклеенный к нему хвост), поэтому
    объединение масок оставляет часть эталона открытой. Метрика обязана назвать
    это нарушением B.

    Здесь эталон намеренно строится вокруг реальной маски: цель зеркала — не
    воспроизвести конкретный дефект системы, а доказать, что сквозной путь
    (координаты PT-1 -> сопоставление с gold -> B) РЕАГИРУЕТ на неполное
    покрытие. Настоящие частичные покрытия на корпусе меряет тест ниже."""
    text = "Договор.\nИНН организации 7707083893.\nКонец.\n"
    i = text.index("7707083893")
    end = i + len("7707083893") + 5          # эталон на 5 символов длиннее маски
    gold = [{"start": i, "end": end, "type": "INN",
             "text": text[i:end], "category": "base"}]
    rec = _run_text_doc(tmp_path, monkeypatch, text, gold)
    inn = [m for m in rec["masks"] if m["gtype"] == "INN" and m["scored"]]
    assert inn, "маска не легла на эталон — зеркало слепо"
    assert any(m["b_ok"] is False for m in inn), \
        "эталон покрыт лишь частично, а метрика B этого не увидела"


@pytest.mark.parametrize("doc_id", ["agency_0002"])
def test_e2e_negative_C_account_masked_as_foreign_type(doc_id):
    """ОТРИЦАТЕЛЬНОЕ сквозное зеркало C на ЗАМОРОЖЕННОМ КОРПУСЕ (ready-made
    нарушение): значение подхватывает чужой regex и прячется под НЕ своим типом.
    Метрика обязана назвать это нарушением C — при том, что по A и B такая маска
    может быть безупречной.

    ПЕРЕНОС ЗЕРКАЛА, ЭТАП 3. Раньше здесь стоял СНИЛС на agency_0003: детектора
    SNILS не было, значение ловил KPP, и это было нарушение C на 185/185 масок
    корпуса. Этап 3 дал СНИЛС свой детектор, нарушение исчезло — тест покраснел
    ровно так, как обещал его собственный докстринг («падение = сигнал перенести
    зеркало на другой случай, а не что метрика сломалась»). Новый носитель
    нарушения — ACCOUNT под KPP на agency_0002 (20 масок, дефект жадности KPP,
    этапом 3 не чинится и в его область не входит)."""
    gold = {d["doc_id"]: d for d in RM.load_gold()}
    assert doc_id in gold, f"{doc_id} исчез из корпуса — зеркало обязано умереть громко"
    rec = RM.process_doc(gold[doc_id])
    bad = [m for m in rec["masks"]
           if m["scored"] and m["gold_type"] == "ACCOUNT" and not m["c_ok"]]
    assert bad, "ACCOUNT под чужим типом не найден — метрика C не различает типы"
    assert all(m["gtype"] != "ACCOUNT" for m in bad)


@pytest.mark.parametrize("doc_id", ["agency_0003"])
def test_e2e_positive_C_snils_is_masked_under_its_own_type(doc_id):
    """ПРИШПИЛИВАЮЩИЙ тест этапа 3, обратная сторона перенесённого зеркала выше:
    СНИЛС теперь маскируется ПОД СВОИМ типом. Если детектор SNILS исчезнет или его
    перехватит KPP/ADDRESS, вернётся нарушение C — и тест покраснеет."""
    gold = {d["doc_id"]: d for d in RM.load_gold()}
    rec = RM.process_doc(gold[doc_id])
    snils = [m for m in rec["masks"] if m["scored"] and m["gold_type"] == "SNILS"]
    assert snils, "ни одна маска не легла на эталонный СНИЛС — детектор потерян"
    assert all(m["c_ok"] for m in snils), \
        "СНИЛС снова маскируется под чужим типом: {}".format(
            [m["gtype"] for m in snils if not m["c_ok"]])


# =========================================================================== #
#      КОНТРАКТЫ БУДУЩИХ ЭТАПОВ — гаснут по мере починок (см. STATE §5)        #
# =========================================================================== #
# Живут ЗДЕСЬ, а не в tests/test_future_contracts.py, намеренно: тот файл держит
# принцип «только публичный вход» (дёргать shifrator.py как процесс и смотреть на
# анонимный текст).  Контракт корректности маскировки такой формой невыразим — он
# требует ЗНАТЬ, что именно система замаскировала, то есть измерительного прибора.
# Тащить прибор в файл, который от него отгорожен, значило бы сломать его принцип;
# поэтому контракты стоят рядом со своими зеркалами.
#
# ОБЛАСТЬ ЗАМЕРА.  Контракты гоняются по ФИКСИРОВАННОМУ подмножеству корпуса, а не
# по всем 324 документам: полный прогон — минуты, ему место в gate.py, а не в
# pytest.  Подмножество подобрано так, чтобы содержать оба интересных класса —
# .docx с таблицами (где plain-сборка схлопывает '\n' внутри ячейки) и .txt.
# Цифра по ПОЛНОМУ корпусу живёт в results_baseline.json и печатается гейтом.
_CONTRACT_DOCS = [
    "agency_0001",   # txt, есть нарушения B
    "agency_0002",   # docx с таблицей: расхождение канала ВНУТРИ маски
    "agency_0004",   # docx, то же
    "supply_0001",   # docx
    "labor_0002",    # txt
    "sale_0005",     # docx
]


@pytest.fixture(scope="module")
def contract_masks():
    """Маски системы по фиксированному подмножеству корпуса (реальный конвейер)."""
    gold = {d["doc_id"]: d for d in RM.load_gold()}
    out = []
    for doc_id in _CONTRACT_DOCS:
        assert doc_id in gold, f"{doc_id} исчез из корпуса — контракт обязан умереть громко"
        rec = RM.process_doc(gold[doc_id])
        assert rec["outcome"] == "processed", f"{doc_id}: {rec}"
        for m in rec["masks"]:
            out.append((doc_id, m))
    assert out, "система не поставила ни одной маски — контракт был бы пустым"
    return out


def test_contract_A_all_masked_values_roundtrip(contract_masks):
    """КОНТРАКТ A (жёсткий, СЕЙЧАС ЗЕЛЁНЫЙ — держать зелёным).

    Каждое значение, которое система замаскировала, decrypt возвращает
    БАЙТ-В-БАЙТ. На полном корпусе на момент написания: 17725/17725 масок, 100%.
    Тест не помечен xfail именно потому, что требование уже выполнено: его роль —
    не дать будущему этапу его уронить (то же делает условие 5 gate.py на полном
    корпусе)."""
    bad = [(d, m["gtype"], m["a_reason"]) for d, m in contract_masks if not m["a_ok"]]
    assert not bad, f"значение восстановилось НЕ байт-в-байт: {bad[:5]}"


def test_contract_A_channel_diffs_land_inside_masks_but_value_is_intact(contract_masks):
    """ДОКАЗАННЫЙ ФАКТ про round-trip на .docx (не предположение).

    На полном корпусе 162 документа (все .docx) дают roundtrip_ok=False. Замер
    показал: все 162 — ws_only, len_mismatch нет ни у одного; расхождения ПОПАДАЮТ
    ВНУТРЬ замаскированных значений (477 масок из 17725), то есть ходовое
    предположение «расхождения только ВНЕ масок» ЛОЖНО. При этом A цел: причина
    расхождения — tokenizer._render_table заменяет '\n' внутри ячейки на пробел
    при сборке plain, а detokenize возвращает значение с исходным '\n'. То есть
    восстановленное значение совпадает с ОРИГИНАЛОМ, а расходится с лоссовой
    plain-сборкой — это артефакт текстового канала, а не порча данных.

    Тест пришпиливает обе половины факта разом: расхождения внутри масок ЕСТЬ, и
    при этом ни одно из них не является нарушением A. Если однажды расхождение
    внутри маски станет порчей значения, упадёт контракт A выше."""
    inside = [(d, m) for d, m in contract_masks if not m["a_channel_ok"]]
    assert inside, (
        "в подмножестве не осталось расхождений канала внутри масок — "
        "доказательство потеряло предмет, подберите документы заново"
    )
    assert all(m["a_ok"] for _d, m in inside)
    assert all(m["a_reason"] == "channel_ws_only" for _d, m in inside)


@pytest.mark.xfail(
    strict=True,
    reason="границы: эталон закрыт масками не целиком (B=77.8% на полном корпусе). "
           "Чинится этапом 2b (регистровые ФИО — худший тип PER, B=62%), этапом 5 "
           "(адрес без маркеров, B=77%) и контрактом SplitEntities (разрыв значения "
           "границей сегмента/ячейки). Снять метку, когда B дойдёт до 100%.",
)
def test_contract_B_every_gold_span_is_fully_covered(contract_masks):
    """КОНТРАКТ B (жёсткий, СЕЙЧАС КРАСНЫЙ).

    Половина сущности под маской, половина открытым текстом — брак вдвойне: и
    утечка, и некорректная маскировка. Человек на проверке её не поймает: он ищет
    ЛИШНЕЕ, а не проверяет, до конца ли спрятано спрятанное.

    ВАЖНО: маска ШИРЕ эталона (over-capture PASSPORT, STATE §4 0c-B) этот контракт
    НЕ роняет — см. TestMirrorB.test_wider_than_gold_is_NOT_a_B_violation."""
    bad = [(d, m["gtype"], m["gold_type"]) for d, m in contract_masks
           if m["scored"] and not m["b_ok"]]
    assert not bad, f"эталонный спан закрыт масками не целиком: {bad[:5]}"


@pytest.mark.xfail(
    strict=True,
    reason="тип: маска ставится под чужим типом (C=85.2% на полном корпусе; SNILS "
           "185/185 прячется под KPP/PHONE — детектора SNILS нет). Чинится этапом 3 "
           "(детекторы SNILS/BIRTHDATE) и этапом 5 (границы адреса, ORG C=36%). "
           "МЯГКИЙ уровень: гейт по C не роняется, этот контракт — только маяк.",
)
def test_contract_C_every_mask_uses_the_right_type(contract_masks):
    """КОНТРАКТ C (мягкий, СЕЙЧАС КРАСНЫЙ). Держится xfail'ом ради видимости
    движения; в gate.py C намеренно НЕ является условием падения."""
    bad = [(d, m["gold_type"], m["gtype"]) for d, m in contract_masks
           if m["scored"] and not m["c_ok"]]
    assert not bad, f"маска под чужим типом: {bad[:5]}"
