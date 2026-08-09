# -*- coding: utf-8 -*-
"""ЭТАП E (часть 1b) — МУЛЬТИСПАН-СБОРЩИК: точечное применение модели Entity.spans.

Собирает сущности, чьё значение разложено на НЕСКОЛЬКО кусков внутри ОДНОГО
сегмента, а фиксированные regex-паттерны такую разбивку не покрывают:

  а) PHONE в произвольной группировке: «+7(916)123-45-67», «84б 82б85 1Ч»
     (омоглифы на краях групп), «8(383)22 1338б» — жёсткая структура 3-3-2-2
     паттерна PHONE не матчит произвольные разбиения; известная утечка ~32%.
  б) реквизиты фиксированной длины и BIRTHDATE, разорванные ПЕРЕНОСОМ СТРОКИ
     внутри одного логического поля (мутация m1337_linebreak): «4070 2810\\n7519 …».
     До этапа A6 здесь был только BANK_ACCOUNT; теперь — общая декларация
     _SEAM_TYPES (счёт/ОГРН/ИНН обоих типов/СНИЛС), потому что причина одна:
     вёрстка рвёт значение, а паттерн через '\\n' не матчится осознанно
     (A2-NEWLINE-CROSS).
  в) ЭТАП A6: те же реквизиты, разорванные ГРАНИЦЕЙ СЕГМЕНТОВ (в .txt строка =
     сегмент, в .docx — абзац). Сборка идёт по граничному окну B3
     (tokenizer._detect_boundary_entities -> collect_seam_entities ниже):
     структурная граница видна из РАЗМЕТКИ (пара сегментов, вид разделителя),
     а не угадывается по символу в тексте.

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

from config_cache import load_yaml_cached
from models import Entity, SourceDocument
from normalizer import detection_view, norm_to_src, normalize_for_detection
from regex_detector import VALIDATORS, _has_anchor

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

# --------------------------------------------------------------------------- #
#   ЭТАП A6 — ДЕКЛАРАЦИЯ ТИПОВ, СОБИРАЕМЫХ ЧЕРЕЗ РАЗРЫВ ('\n' и стык окна)     #
# --------------------------------------------------------------------------- #
# Участвует тип, у которого (1) суммарная длина значения в цифрах ФИКСИРОВАНА
# и (2) есть якорь-слово. Для сборки якорь ОБЯЗАТЕЛЕН у всех — даже там, где
# посегментная детекция обходится контрольной суммой (ИНН): сборка рискованнее
# обычного матча, требования строже. КС берётся из entity_types.yaml
# (validate:) и проверяется ВДОБАВОК к длине, где определена.
#
# Кого здесь НЕТ и почему (осознанные исключения, не забывчивость):
#   * PHONE — телефоны в столбик: два номера через '\n' склеились бы в один
#     (документированный запрет секции (а) выше). Длины 10/11 без КС.
#   * PASSPORT — значение неоднородно («серия 12 34 № 567890»), сборка голой
#     цифровой цепи проигнорировала бы форму, а anti_anchor («паспорт
#     КАЧЕСТВА») в цепь не встроен. Отдельная задача, если замер покажет класс.
#   * BIK/KPP — 9 цифр без КС: слишком короткие и частые, чтобы собирать их
#     через структурную границу по одной длине (направление ошибки).
# Длины всех участников попарно различны, поэтому цепь однозначно решается в
# ноль или один тип.
_SEAM_TYPES = (
    ("BANK_ACCOUNT", (20,)),
    ("OGRN", (13, 15)),
    ("INN", (10,)),
    ("INN_PERSON", (12,)),
    ("SNILS", (11,)),
)

# У СНИЛС якорь живёт не в поле anchor:, а внутри самого паттерна (span_group,
# этап 3) — для сборки нужен отдельный компилят. Зеркало головы паттерна из
# entity_types.yaml (SNILS), длинный хвост «…пенсионного страхования» для окна
# в 40 символов не нужен.
_LOCAL_SEAM_ANCHORS = {
    "SNILS": re.compile(r"(?i)(?:снилс|страхов\w*\s+свидетельств\w*)"),
}

#: кэш _load_seam_spec по пути конфига (окон в документе тысячи, компилировать
#: якоря на каждое нельзя).
_SEAM_SPEC_CACHE: dict[str, list[tuple]] = {}


def _load_seam_spec(config_path: str) -> list[tuple]:
    """[(entity_type, totals, validator|None, anchor_re)] для типов сборки.
    Тип без якоря (и без локального зеркала) в сборку не попадает вовсе."""
    cached = _SEAM_SPEC_CACHE.get(config_path)
    if cached is not None:
        return cached
    config = load_yaml_cached(config_path)
    spec: list[tuple] = []
    for etype, totals in _SEAM_TYPES:
        s = config["entity_types"].get(etype) or {}
        if s.get("enabled") is False or "token_prefix" not in s:
            continue
        anchor = s.get("anchor")
        anchor_re = re.compile(anchor) if anchor else _LOCAL_SEAM_ANCHORS.get(etype)
        if anchor_re is None:
            continue
        validator = VALIDATORS.get(s.get("validate") or "")
        spec.append((etype, totals, validator, anchor_re))
    _SEAM_SPEC_CACHE[config_path] = spec
    return spec


def _eff_digits(s: str) -> str:
    return "".join(_LOOKALIKE.get(ch, ch) for ch in s)


def _chain_parts_at_newlines(norm: str, chain) -> list[list]:
    """Части цепи, разделённые разрывами с '\\n' внутри (границы строк/стыки)."""
    parts = [[chain[0]]]
    for prev, g in zip(chain, chain[1:]):
        if "\n" in norm[prev.end():g.start()]:
            parts.append([g])
        else:
            parts[-1].append(g)
    return parts


def _part_is_complete(part, totals, validator) -> bool:
    dg = _eff_digits("".join(g.group(0) for g in part))
    return len(dg) in totals and (validator is None or validator(dg))


def _match_seam_type(norm: str, chain, spec) -> str | None:
    """Тип, чьим ровно ОДНИМ значением является вся цепь, либо None.

    Стражи направления ошибки (лучше не найти, чем захватить лишнее):
      1. сумма цифр цепи ТОЧНО равна длине одного значения типа (зеркало «−»
         секции (а): два значения дают двойную длину -> отказ целиком, под-окна
         запрещены);
      2. якорь типа непосредственно слева от начала цепи (_has_anchor: та же
         строка, без цифр в разрыве) — здесь он ОБЯЗАТЕЛЕН у всех типов, в
         отличие от посегментной детекции;
      3. НИ ОДНА часть цепи по свою сторону '\\n'-разрыва не является целым
         валидным значением сама по себе — иначе стык паразитный: целый ОГРН-13
         плюс случайные «15» на следующей строке дали бы «валидный» ОГРНИП-15.
         Это формализация урока A2-NEWLINE-CROSS: через настоящую границу не
         сшивается то, что цело уже по одну её сторону.

    КОНТРОЛЬНОЙ СУММЫ СРЕДИ СТРАЖЕЙ НЕТ, И ЭТО ПРАВКА ЭТАПА CROSSTYPE
    (`DEFGATE-CROSSTYPE-RESIDUE`, 4 случая из 6). Она была здесь шлагбаумом и
    противоречила инварианту этапа 4 (regex_detector.detect_regex, «ЭТАП 4 — КС
    как сигнал уверенности, а не шлагбаум»): под якорем «ИНН» значение с не
    сошедшейся КС — всё равно ПДн, опечатка в номере не делает номер не-ПДн.
    Посегментная дорога это и делала; дорога сборки — нет, и расхождение стоило
    утечки ЦЕЛОГО КЛАССА: у 12-значного ИНН физлица, разорванного переносом,
    сборка отказывала по КС, а голову из 10 цифр подбирал паттерн ИНН
    ОРГАНИЗАЦИИ — тип неверный, на максимуме маска есть, а в наборе по
    умолчанию (ИНН юрлица выключен) значение уходило открытым.

    Ослаблением это не является: КС снята ТОЛЬКО там, где её и без того
    перебивал бы якорь, а якорь на дороге сборки обязателен для всех типов.
    Длины участников _SEAM_TYPES попарно различны, поэтому КС не участвовала и
    в РАЗЛИЧЕНИИ типов — снятие не может переназвать один тип другим.
    В страже 3 КС ОСТАЁТСЯ: там она отвечает на другой вопрос — «стык
    паразитный?», и доказательством паразитности служит именно ЦЕЛОЕ ВАЛИДНОЕ
    значение по одну сторону разрыва, а не любой прогон нужной длины."""
    digits = _eff_digits("".join(g.group(0) for g in chain))
    for etype, totals, validator, anchor_re in spec:
        if len(digits) not in totals:
            continue
        if not _has_anchor(norm, chain[0].start(), anchor_re):
            continue
        parts = _chain_parts_at_newlines(norm, chain)
        if len(parts) > 1 and any(
                _part_is_complete(p, totals, validator) for p in parts):
            continue
        return etype
    return None


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
    """Мультиспан-сущности документа (PHONE-группировки, \\n-рваные реквизиты
    _SEAM_TYPES и BIRTHDATE). Дубли со штатными regex-матчами разрешает общий
    алгоритм пересечений блока 4 (более длинный hull побеждает; равный — один
    токен)."""
    seam_spec = _load_seam_spec(config_path)
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

        # --- (б) реквизит фикс. длины, рваный переносом строки ВНУТРИ сегмента
        # (ячейка docx: '\n' между абзацами ячейки / w:br). До A6 — только
        # BANK_ACCOUNT; условия на тип теперь в _match_seam_type, для счёта они
        # ровно прежние (якорь + 20 цифр; страж частей для длины 20 недостижим:
        # 20 = a + b при a,b > 0 не оставляет целой части). ---
        for chain in _chains(norm, _ACC_SEP):
            if len(chain) < 2:
                continue
            if "\n" not in norm[chain[0].start():chain[-1].end()]:
                continue   # без \n внутри — случай штатного паттерна
            etype = _match_seam_type(norm, chain, seam_spec)
            if etype is not None:
                out.append(_emit(segment, norm, omap, chain, etype))

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


def collect_seam_entities(window_text: str, tail_end: int, head_start: int,
                          config_path: str) -> list[tuple[int, int, str]]:
    """ЭТАП A6 — значение, разорванное СТЫКОМ граничного окна B3.

    Окно (tokenizer._detect_boundary_entities) — «хвост сегмента A + '\\n' +
    голова сегмента B»; regex-проход по блобу через '\\n' не матчится осознанно
    (A2-NEWLINE-CROSS), поэтому реквизит, рваный границей сегментов-строк,
    не находил никто (остаток A5: ACCOUNT/OGRN). Здесь та же сборка цепи, что в
    секции (б) collect_multispan, но по тексту ОКНА; берутся ТОЛЬКО цепи,
    пересекающие точку стыка [tail_end, head_start) — всё остальное покрыто
    посегментной детекцией и мультиспаном и было бы дублем.

    Возвращает [(start, end, entity_type)] в координатах СЫРОГО текста окна;
    деление на пару Entity_A/Entity_B с реальными оффсетами сегментов делает
    шаг 4 _detect_boundary_entities (контракт B3, обратимость доказана
    round-trip-тестами). BIRTHDATE собирается теми же _BDATE-паттернами, что и
    внутри сегмента (якорь рождения в самом регэкспе)."""
    norm, omap = normalize_for_detection(window_text)
    if not norm:
        return []
    seam_spec = _load_seam_spec(config_path)
    out: list[tuple[int, int, str]] = []
    for chain in _chains(norm, _ACC_SEP):
        if len(chain) < 2:
            continue
        ws, we = norm_to_src(omap, chain[0].start(), chain[-1].end())
        if not (ws < tail_end and we > head_start):
            continue   # стык не пересечён — зона посегментной детекции
        etype = _match_seam_type(norm, chain, seam_spec)
        if etype is not None:
            out.append((ws, we, etype))
    for rex in (_BDATE_LEFT_RE, _BDATE_RIGHT_RE):
        for m in rex.finditer(norm):
            ws, we = norm_to_src(omap, m.start(1), m.end(1))
            if ws < tail_end and we > head_start:
                out.append((ws, we, "BIRTHDATE"))
    return out
