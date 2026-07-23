# -*- coding: utf-8 -*-
"""ЭТАП E (часть 1b) — МУЛЬТИСПАН-СБОРЩИК: точечное применение модели Entity.spans.

Собирает сущности, чьё значение разложено на НЕСКОЛЬКО кусков внутри ОДНОГО
сегмента, а фиксированные regex-паттерны такую разбивку не покрывают:

  а) PHONE в произвольной группировке: «+7(916)123-45-67», «84б 82б85 1Ч»
     (омоглифы на краях групп), «8(383)22 1338б» — жёсткая структура 3-3-2-2
     паттерна PHONE не матчит произвольные разбиения; известная утечка ~32%.
  б) BANK_ACCOUNT и BIRTHDATE, разорванные ПЕРЕНОСОМ СТРОКИ внутри одного
     логического поля (мутация m1337_linebreak): «4070 2810\\n7519 …».

ПРИНЦИП ЦЕЛОСТНОСТИ (приёмка 1a важнее recall): маскируется HULL — непрерывный
диапазон от начала первого куска до конца последнего, одним токеном;
original_text = срез сегмента по hull (разделители между кусками входят
байт-в-байт). spans хранят диапазоны САМИХ кусков (для сессии/метрик/будущей
сборки). Ничего не «сшивается» из разных логических полей:

  * ЗЕРКАЛО (−): два разных значения НЕ склеиваются — цепочка групп принимается
    ТОЛЬКО если её суммарная длина в цифрах точно равна длине ОДНОГО значения
    типа (телефон 10-11, счёт 20). Две склеенные сущности дают 2x длину ->
    цепочка отвергается ЦЕЛИКОМ (никаких под-окон: под-окно внутри чужого
    длинного числа — ложный телефон внутри счёта).
  * PHONE не цепляется через \\n (телефон в столбик из двух РАЗНЫХ номеров не
    сольётся); ACCOUNT НАОБОРОТ обязан иметь \\n в разрыве (иначе его ловит
    штатный паттерн) И якорь счёта слева (правило этапа 4: голое 20-значное
    число не берётся).

Детекторы правил ORG/PER/ADDRESS не трогаются (граница сессии): сборщик работает
только по regex-типам, на том же detection_view, что и detect_regex.
"""

import re
import uuid

import yaml

from models import Entity, SourceDocument
from normalizer import detection_view, norm_to_src
from regex_detector import _has_anchor

# Цифро-подобная группа: цифры + омоглифы цифр, НЕ сведённые нормализацией
# (край токена 'б', группа с <2 настоящими цифрами — см. normalizer._LOOKALIKE_*).
# Требование >=1 настоящей цифры — в коде (иначе слово «Обз» стало бы группой).
_GROUP_RE = re.compile(r"[0-9ОоOoЗзЧчІіб]+")

# Отображение омоглиф -> цифра для ПОДСЧЁТА эффективных цифр (само значение в
# тексте не правится — маскируется исходный срез).
_LOOKALIKE = {"О": "0", "о": "0", "O": "0", "o": "0",
              "З": "3", "з": "3", "Ч": "4", "ч": "4",
              "І": "1", "і": "1", "б": "6"}

# Разделители МЕЖДУ группами одного значения. PHONE: без \n (телефон не цепляется
# через перенос — два номера в столбик не сольются); ACCOUNT: с \n (ровно его и
# чиним). Максимум 3 символа разрыва: «) », « - », «-\n ».
_PHONE_SEP = frozenset(" \t()-–—+")
_ACC_SEP = frozenset(" \t\n\r-–—")
_MAX_GAP = 3

_PHONE_TOTALS = (10, 11)
_ACC_TOTAL = 20


def _eff_digits(s: str) -> str:
    return "".join(_LOOKALIKE.get(ch, ch) for ch in s)


def _chains(norm: str, sep_chars: frozenset):
    """Максимальные цепочки цифро-подобных групп, разделённых ТОЛЬКО символами
    sep_chars (разрыв 1..3 символа). Возвращает список цепочек; цепочка — список
    матчей _GROUP_RE. Группа без единой настоящей цифры цепочку рвёт."""
    groups = [m for m in _GROUP_RE.finditer(norm)
              if any(ch.isdigit() for ch in m.group(0))]
    chains: list[list] = []
    cur: list = []
    for g in groups:
        if cur:
            gap = norm[cur[-1].end():g.start()]
            if 0 < len(gap) <= _MAX_GAP and all(ch in sep_chars for ch in gap):
                cur.append(g)
                continue
            chains.append(cur)
        cur = [g]
    if cur:
        chains.append(cur)
    return chains


def _emit(segment, norm, omap, chain, entity_type, plus_prefix=False):
    """Entity из цепочки групп: hull [первая..последняя], spans — куски."""
    ns, ne = chain[0].start(), chain[-1].end()
    if plus_prefix and ns > 0 and norm[ns - 1] == "+":
        ns -= 1
    src_s, src_e = norm_to_src(omap, ns, ne)
    spans = []
    for k, g in enumerate(chain):
        gs, ge = (ns, g.end()) if k == 0 else (g.start(), g.end())
        ss, se = norm_to_src(omap, gs, ge)
        spans.append((ss, se))
    return Entity(
        id=str(uuid.uuid4()),
        segment_id=segment.id,
        start=src_s,
        end=src_e,
        original_text=segment.text[src_s:src_e],
        entity_type=entity_type,
        detector="regex",
        confidence=1.0,
        spans=spans if len(spans) > 1 else None,
    )


def _load_account_anchor(config_path: str):
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    spec = config["entity_types"].get("BANK_ACCOUNT") or {}
    a = spec.get("anchor")
    return re.compile(a) if a else None


# Дата, разорванная переносом, ПОД ЯКОРЕМ РОЖДЕНИЯ (правило этапа 3: дата без
# якоря — не BIRTHDATE). Те же якоря, что в entity_types.yaml, но зазор и
# внутренность значения допускают \n. Обязателен \n ВНУТРИ значения — иначе
# случай штатного паттерна (не дублируем).
_BDATE_LEFT_RE = re.compile(
    r"(?i)(?:дат[аыу]\s*рожд[а-я]*|года?\s*рождения|рожд(?:ени[а-я]*|\.))"
    r"[^\d]{0,12}(\d{1,2}[ \n]{0,2}[.\/][ \n]{0,2}\d{1,2}[ \n]{0,2}[.\/][ \n]{0,2}\d(?:[ \n]?\d){3})(?!\d)"
)
_BDATE_RIGHT_RE = re.compile(
    r"(?i)(\d{1,2}[ \n]{0,2}[.\/][ \n]{0,2}\d{1,2}[ \n]{0,2}[.\/][ \n]{0,2}\d(?:[ \n]?\d){3})"
    r"\s*(?:г\.\s?р\.|г\.\s?рожд)"
)


def _split_on_newlines(segment, src_s, src_e):
    """Куски hull, разделённые прогонами \\n/\\r (границы кусков подрезаны от
    пробелов). Для BIRTHDATE: spans = непереносные части даты."""
    text = segment.text[src_s:src_e]
    spans = []
    for m in re.finditer(r"[^\n\r]+", text):
        piece = m.group(0)
        lead = len(piece) - len(piece.lstrip(" \t"))
        trail = len(piece) - len(piece.rstrip(" \t"))
        s = src_s + m.start() + lead
        e = src_s + m.end() - trail
        if e > s:
            spans.append((s, e))
    return spans


def collect_multispan(doc: SourceDocument, config_path: str) -> list[Entity]:
    """Мультиспан-сущности документа (PHONE-группировки, \\n-рваные ACCOUNT и
    BIRTHDATE). Дубли со штатными regex-матчами разрешает общий алгоритм
    пересечений блока 4 (более длинный hull побеждает; равный — один токен)."""
    acc_anchor = _load_account_anchor(config_path)
    out: list[Entity] = []

    for segment in doc.segments:
        if not segment.text:
            continue
        norm, omap = detection_view(segment)
        if not norm:
            continue

        # --- (а) PHONE: произвольная группировка на одной строке ---
        for chain in _chains(norm, _PHONE_SEP):
            if len(chain) < 2:
                continue   # непрерывный номер — зона штатного паттерна PHONE
            digits = _eff_digits("".join(g.group(0) for g in chain))
            if len(digits) not in _PHONE_TOTALS or digits[0] not in "78":
                continue   # не длина ОДНОГО телефона -> ничего не склеиваем (зеркало −)
            out.append(_emit(segment, norm, omap, chain, "PHONE", plus_prefix=True))

        # --- (б) BANK_ACCOUNT: рваный переносом строки, под якорем счёта ---
        if acc_anchor is not None:
            for chain in _chains(norm, _ACC_SEP):
                if len(chain) < 2:
                    continue
                if "\n" not in norm[chain[0].start():chain[-1].end()]:
                    continue   # без \n внутри — случай штатного паттерна
                digits = _eff_digits("".join(g.group(0) for g in chain))
                if len(digits) != _ACC_TOTAL:
                    continue   # два счёта в столбик дадут 40 -> отвергаем целиком
                if not _has_anchor(norm, chain[0].start(), acc_anchor):
                    continue   # правило этапа 4: без якоря счёт не берётся
                out.append(_emit(segment, norm, omap, chain, "BANK_ACCOUNT"))

        # --- (б) BIRTHDATE: дата, рваная переносом, под якорем рождения ---
        for rex in (_BDATE_LEFT_RE, _BDATE_RIGHT_RE):
            for m in rex.finditer(norm):
                if "\n" not in m.group(1):
                    continue   # без \n — случай штатного паттерна
                src_s, src_e = norm_to_src(omap, m.start(1), m.end(1))
                spans = _split_on_newlines(segment, src_s, src_e)
                out.append(Entity(
                    id=str(uuid.uuid4()),
                    segment_id=segment.id,
                    start=src_s,
                    end=src_e,
                    original_text=segment.text[src_s:src_e],
                    entity_type="BIRTHDATE",
                    detector="regex",
                    confidence=1.0,
                    spans=spans if len(spans) > 1 else None,
                ))

    return out
