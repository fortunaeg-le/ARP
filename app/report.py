"""Этап U4 — метрики по накопленной разметке и ОТЧЁТ ДЛЯ РАЗРАБОТЧИКА без ПДн.

Зачем. Пользователь пилота проверяет документы на экране проверки (U3) и попутно
копит разметку: «здесь пропущено», «здесь ложная маска», «здесь съехала граница»,
«здесь неверный тип». Это единственный источник правды о качестве движка НА ЕГО
документах — но сама разметка содержит фрагменты реального текста (`value`) и
наружу уйти не может. Отсюда две разные вещи, которые этот модуль строго
разделяет:

  * МЕТРИКИ (задачи 1 и 4) — считаются локально, показываются владельцу на экране
    «Аналитика». Это числа, ПДн в них нет по построению.
  * ОТЧЁТ (задачи 2 и 3) — файл, который пользователь скачивает и отправляет
    разработчику. Содержит те же числа плюс СТРУКТУРНЫЕ КОДЫ находок («тип
    PERSON, абзац 12, длина 14, есть инициалы, детектор не сработал») вместо
    текста. Ни одного фрагмента документа.

--- Почему отчёт безопасен ПО ПОСТРОЕНИЮ, а не по внимательности ----------------

«Не забыть не положить текст» — не механизм. Здесь их три, и каждый следующий
ловит промах предыдущего:

  1. БЕЛЫЙ СПИСОК. Отчёт собирается не «из записи разметки минус лишнее», а с
     нуля: каждое поле findings перечислено в `_FINDING_FIELDS`, и собирает его
     функция, которая физически возвращает только int/bool/значение из закрытого
     словаря. Полю `value` неоткуда взяться — его никто не кладёт.
  2. НОРМАЛИЗАЦИЯ НА ВХОДЕ. Всё, что пришло из хранилища строкой (entity_type,
     detector, kind, build_mark, created_at), прогоняется через `_norm_*`:
     значение вне известного словаря становится "unknown", дата — переразбирается
     datetime и печатается заново. То есть в отчёт попадают строки, ПОРОЖДЁННЫЕ
     кодом, а не пронесённые из данных.
  3. СТРУКТУРНЫЙ ЧАСОВОЙ (`assert_structural`). Готовый отчёт обходится целиком,
     и КАЖДАЯ строка — и ключи, и значения — обязана принадлежать закрытому
     множеству: словарь кодов, схемные ключи, ISO-дата, слаг сборки, либо
     дословно один из текстов-констант этого модуля (`_FIXED_PROSE`). Иначе
     `ReportLeakError` и отчёт не отдаётся вообще. Если завтра кто-то допишет в
     находку поле с фрагментом текста — упадёт здесь, а не у клиента.

Тест приёмки (`tests/test_u4_report.py`) бьёт по всем трём: документ с
маркерными строками → отчёт → маркера нет ни в json, ни в md, ни в предпросмотре;
плюс само-тест часового (искусственно внесённый текст обязан уронить сборку).

--- Ограничения метрики (названы и в самом отчёте, не только здесь) -------------

Метрика считается по РУЧНОЙ РАЗМЕТКЕ, а не по эталонному корпусу, и это меняет
смысл чисел:

  * precision здесь — «доля масок, которые проверяющий не отклонил». Маску, на
    которую он не посмотрел, мы считаем верной. Это ВЕРХНЯЯ ОЦЕНКА.
  * recall здесь — «найдено / (найдено + замеченные пропуски)». Пропуск, который
    проверяющий не заметил, в знаменатель не входит. Тоже ВЕРХНЯЯ ОЦЕНКА.
  * выборка мала и смещена: документ, в котором пользователь не отметил НИЧЕГО,
    попадает в знаменатель только если отчёт строится по этому документу, пока
    жива его сессия (см. ниже). В агрегате за период преобладают документы, где
    было что отмечать.

Ничего из этого не заменяет полный замер на корпусе (`tests/corpus/gate.py`).

--- Откуда берётся знаменатель ---------------------------------------------------

Сколько масок всего поставил движок — это НЕ разметка, это состояние сессии. А
сессия живёт 24 часа, разметка — 30 дней (этап S1). Значит для разметки старше
суток сессии уже нет и знаменатель взять неоткуда. Поэтому `app/core.py` кладёт
в КАЖДУЮ запись разметки компактный слепок `census` (сколько масок всего, по
типам, по детекторам) на момент правки; отчёт берёт слепок САМОЙ РАННЕЙ записи
сессии — это состояние ДО ручных правок, то есть чистый результат автоматики.
Для разметки, сделанной до этапа U4, слепка нет — пробуем прочитать живую
сессию, а если её уже нет, документ честно считается «без знаменателя» и входит
в отчёт только абсолютными числами правок (поле `documents_without_denominator`).

--- Границы ---------------------------------------------------------------------

Ничего никуда не отправляется: модуль не умеет в сеть, отчёт только возвращается
интерфейсу и скачивается пользователем. `src/` не трогается — только чтение
через `storage.py`. Единственное место, где мы идём в файловую систему мимо
функции хранилища, — перечисление session_id, у которых есть разметка
(`_markup_session_ids`): в `storage.py` нет такой функции, а добавить её нельзя
в границах этой сессии. Путь берётся у самого хранилища
(`storage.default_markup_dir()`), содержимое читается `storage.list_markup()`.
Когда `src/` снова будет свободен — правильное место для `list_markup_sessions()`
именно там (см. HANDOFF_U4).
"""

import re
from datetime import datetime

import core

REPORT_VERSION = "1"

# --------------------------------------------------------------------------- #
# Закрытые словари. Всё, что отчёт имеет право сказать строкой, перечислено
# здесь — часовой (`assert_structural`) не пропустит ничего сверх этого.
# --------------------------------------------------------------------------- #

UNKNOWN = "unknown"

#: Виды правок разметки (U3, app/core.py). Порядок — порядок вывода в отчёте.
KINDS = ("missed", "false_mask", "boundary", "type")
KIND_LABELS = {
    "missed": "пропущено (маски не было)",
    "false_mask": "ложная маска (не ПДн)",
    "boundary": "поправлена граница",
    "type": "поправлен тип",
}

#: Типы ПДн — ровно те, что знает интерфейс; плюс "unknown" для чужих значений.
ENTITY_TYPES = tuple(core.TYPE_LABELS) + (UNKNOWN,)

#: Механизмы детекции. "none" — для пропусков: маску не поставил НИКТО, это
#: содержательное значение, а не отсутствие данных.
DETECTORS = ("regex", "ner", "manual", "none", UNKNOWN)
DETECTOR_LABELS = {
    "regex": "регулярные выражения + якоря",
    "ner": "NER / структурные якоря",
    "manual": "ручная маска пользователя",
    "none": "не сработал ни один детектор",
    UNKNOWN: "механизм неизвестен (разметка до этапа S1)",
}

#: Структурный контейнер фрагмента (из segment_id, см. src/extractor.py).
CONTAINERS = ("paragraph", "table_cell", "text_line", UNKNOWN)
CONTAINER_LABELS = {
    "paragraph": "абзац",
    "table_cell": "ячейка таблицы",
    "text_line": "строка txt",
    UNKNOWN: "неизвестно",
}

#: Регистровая форма фрагмента — признак класса проблемы (см. этап 2b: сплошь
#: строчные ФИО детектировались ровно в 0% случаев).
CASE_SHAPES = ("upper", "lower", "title", "mixed", "no_letters")

SCOPES = ("session", "all")

#: ЭТАП T1 — наборы типов (`src/type_policy.PROFILES`) плюс два служебных кода:
#: "mixed" — документы выборки маскировались РАЗНЫМИ наборами (или часть без
#: слепка), поэтому один состав назвать нельзя; "unknown" — слепка нет ни у
#: одного документа (разметка старше этапа T1). Оба содержательны: отчёт обязан
#: отличать «состав неизвестен» от «состав полный», иначе ноль масок типа читается
#: как «детектор не нашёл», хотя тип мог быть просто выключен.
MIXED = "mixed"
POLICY_PROFILES = ("personal", "personal_requisites", "with_money", "maximum",
                   MIXED, UNKNOWN)
POLICY_PROFILE_LABELS = {
    "personal": "Только персональные данные",
    "personal_requisites": "Персональные данные и реквизиты",
    "with_money": "Всё, включая деньги",
    "maximum": "Максимум (все типы)",
    MIXED: "разные наборы в выборке — единый состав назвать нельзя",
    UNKNOWN: "состав не записан (разметка старше этапа T1)",
}

#: Белый список полей находки. Единственное место, где решается, ЧТО вообще
#: может оказаться в findings. Добавление поля сюда — осознанное действие,
#: которое обязано пройти часового.
_FINDING_FIELDS = (
    "document", "kind", "entity_type", "detector",
    "container", "container_index", "row", "col",
    "length_chars", "tokens", "has_digits", "has_latin", "has_cyrillic",
    "has_punct", "has_initials", "case_shape", "build",
)


# --------------------------------------------------------------------------- #
# Тексты-константы. Часовой сверяет строки отчёта с этим множеством ДОСЛОВНО,
# поэтому любая прозаическая строка в отчёте обязана быть здесь.
# --------------------------------------------------------------------------- #

CONTENTS_INCLUDED = (
    "Числа: сколько масок поставил движок, сколько правок сделал проверяющий, в каком соотношении.",
    "Структурные коды находок: тип ПДн, механизм детекции, где в документе (абзац/ячейка), длина и признаки фрагмента.",
    "Разбивка по типам ПДн, по механизмам детекции и по версиям сборки.",
)

CONTENTS_EXCLUDED = (
    "Ни одного фрагмента текста документа — ни в открытом виде, ни в обезличенном, ни в сокращённом.",
    "Ни имён, ни организаций, ни номеров, ни адресов — даже частично, даже посимвольно.",
    "Ни имени файла, ни идентификаторов сессий, ни путей на диске.",
)

LIMITATIONS = (
    "Метрика считается по РУЧНОЙ РАЗМЕТКЕ проверяющего, а не по эталонному корпусу.",
    "precision — доля масок, которые проверяющий не отклонил; маска, на которую он не посмотрел, считается верной. Это верхняя оценка.",
    "recall — найдено / (найдено + ЗАМЕЧЕННЫЕ пропуски); незамеченный пропуск в знаменатель не входит. Это верхняя оценка.",
    "Выборка мала и смещена: в агрегате за период преобладают документы, в которых было что отмечать.",
    "Документы, чья сессия истекла до этапа U4, входят в отчёт без знаменателя — только абсолютными числами правок.",
    "Эти числа не заменяют полный замер на корпусе и не являются приёмочными.",
    "Считайте числа только по ВКЛЮЧЁННЫМ типам (раздел «Состав»): у выключенного типа маска не ставится вовсе, и ноль в его строке — это настройка, а не работа детектора.",
)

_SCHEMA_NOTE = "Отчёт собран по белому списку полей; текст документа в него не попадает по построению."

#: Всё прозаическое, что может встретиться в отчёте (значения-строки).
_FIXED_PROSE = frozenset(
    CONTENTS_INCLUDED + CONTENTS_EXCLUDED + LIMITATIONS + (_SCHEMA_NOTE,)
)

#: Схемные ключи отчёта — второй столб часового (ключи тоже строки).
_SCHEMA_KEYS = frozenset({
    "report_version", "generated_at", "tool_build", "scope", "schema_note",
    "period", "from", "to",
    "contents", "included", "excluded", "limitations",
    "sample", "documents", "documents_with_denominator",
    "documents_without_denominator", "edits",
    "totals", "masks_total", "missed", "false_mask", "boundary", "type",
    "unknown", "edits_total",
    "metrics", "precision_reviewed_pct", "recall_reviewed_pct",
    "boundary_edit_rate_pct", "type_error_rate_pct",
    "by_type", "by_detector", "by_build", "findings",
    "masks", "documents_touched",
    # ЭТАП T1 — состав включённых типов (шаг 4 задания).
    "policy", "profile", "enabled_types", "disabled_types", "documents_without_policy",
}) | frozenset(_FINDING_FIELDS)

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2})?$")
#: Слаг сборки: ASCII-идентификатор. Кириллица/пробелы/кавычки не проходят —
#: значит подсунуть сюда фрагмент документа нельзя даже теоретически.
_BUILD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ReportLeakError(RuntimeError):
    """Отчёт содержит строку вне закрытого словаря — сборка прервана.

    Это не «предупреждение о возможной утечке», это отказ отдать отчёт. Ловится
    в server.py и показывается пользователю как ошибка, а не как отчёт."""


# --------------------------------------------------------------------------- #
# Нормализация входа: строка из хранилища -> код из словаря.
# --------------------------------------------------------------------------- #

def _norm_kind(value) -> str:
    return value if value in KINDS else UNKNOWN


def _norm_type(value) -> str:
    return value if value in core.TYPE_LABELS else UNKNOWN


def _norm_detector(value) -> str:
    return value if value in DETECTORS else UNKNOWN


def _norm_build(value) -> str:
    return value if isinstance(value, str) and _BUILD_RE.match(value) else UNKNOWN


def _norm_iso(value) -> str | None:
    """Переразбирает дату и печатает ЗАНОВО: в отчёт уходит строка, порождённая
    кодом, а не пронесённая из файла разметки."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Структурные коды находки. Из фрагмента текста наружу выходят ТОЛЬКО числа и
# булевы признаки — ни одного символа самого фрагмента.
# --------------------------------------------------------------------------- #

_INITIALS_RE = re.compile(r"[А-ЯЁA-Z]\s*\.\s*[А-ЯЁA-Z]\s*\.")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _case_shape(value: str) -> str:
    letters = [c for c in value if c.isalpha()]
    if not letters:
        return "no_letters"
    if all(c.isupper() for c in letters):
        return "upper"
    if all(c.islower() for c in letters):
        return "lower"
    words = [w for w in value.split() if any(c.isalpha() for c in w)]
    if words and all(w[0].isupper() for w in words if w[0].isalpha()):
        return "title"
    return "mixed"


def _value_shape(value) -> dict:
    """Признаки фрагмента БЕЗ фрагмента. Возвращает только int/bool/код."""
    if not isinstance(value, str):
        value = ""
    return {
        "length_chars": len(value),
        "tokens": len(value.split()),
        "has_digits": any(c.isdigit() for c in value),
        "has_latin": bool(re.search(r"[A-Za-z]", value)),
        "has_cyrillic": bool(re.search(r"[А-Яа-яЁё]", value)),
        "has_punct": bool(_PUNCT_RE.search(value)),
        "has_initials": bool(_INITIALS_RE.search(value)),
        "case_shape": _case_shape(value),
    }


_SEG_PARAGRAPH_RE = re.compile(r"^p(\d+)$")
_SEG_CELL_RE = re.compile(r"^t(\d+)_r(\d+)_c(\d+)$")
_SEG_LINE_RE = re.compile(r"^l(\d+)$")


def _context(segment_id) -> dict:
    """segment_id -> структурное место в документе (см. src/models.py TextSegment.id).

    Индексы — числа, они структурны и содержимого не раскрывают; сам segment_id
    в отчёт НЕ попадает (он строка из данных, а строки из данных мы не носим)."""
    sid = segment_id if isinstance(segment_id, str) else ""
    m = _SEG_PARAGRAPH_RE.match(sid)
    if m:
        return {"container": "paragraph", "container_index": int(m.group(1)), "row": None, "col": None}
    m = _SEG_CELL_RE.match(sid)
    if m:
        return {"container": "table_cell", "container_index": int(m.group(1)),
                "row": int(m.group(2)), "col": int(m.group(3))}
    m = _SEG_LINE_RE.match(sid)
    if m:
        return {"container": "text_line", "container_index": int(m.group(1)), "row": None, "col": None}
    return {"container": UNKNOWN, "container_index": None, "row": None, "col": None}


# --------------------------------------------------------------------------- #
# Сбор входных данных.
# --------------------------------------------------------------------------- #

def _markup_session_ids() -> list[str]:
    """session_id, у которых есть накопленная разметка (отсортированы).

    Единственный поход в ФС мимо функции хранилища — в storage.py нет
    `list_markup_sessions()`, а добавлять её нельзя в границах этой сессии
    (см. докстринг модуля §Границы). Путь спрашиваем у самого хранилища."""
    from storage import default_markup_dir

    store = default_markup_dir()
    if not store.exists():
        return []
    suffix = ".markup.json"
    return sorted(p.name[: -len(suffix)] for p in store.glob(f"*{suffix}"))


def _live_census(session_id: str) -> dict | None:
    """Слепок масок из ЖИВОЙ сессии (если она ещё не истекла и не удалена).

    Запасной путь для разметки, сделанной до этапа U4 (в ней нет своего
    census). Любая ошибка хранилища — это просто «знаменателя нет», не отказ."""
    try:
        from storage import load_session
        session = load_session(session_id)
    except Exception:  # noqa: BLE001 — истекла/нет/битая: все три = знаменателя нет
        return None
    return census_from_session(session)


def census_from_session(session: dict) -> dict:
    """Слепок «сколько масок и какие» из формата сессии. Живёт здесь, а зовётся
    из app/core.py при сохранении КАЖДОЙ записи разметки — чтобы знаменатель
    пережил 24-часовую сессию (см. докстринг модуля §Откуда берётся знаменатель)."""
    by_type: dict[str, int] = {}
    by_detector: dict[str, int] = {}
    total = 0
    for rec in session.get("entities", []):
        etype = _norm_type(rec.get("entity_type"))
        for occ in rec.get("occurrences", []):
            total += 1
            by_type[etype] = by_type.get(etype, 0) + 1
            det = _norm_detector(occ.get("detector", rec.get("detector")))
            by_detector[det] = by_detector.get(det, 0) + 1
    return {"masks_total": total, "by_type": by_type, "by_detector": by_detector}


def _policy_block(docs: list[dict]) -> dict:
    """ЭТАП T1 — СОСТАВ ВКЛЮЧЁННЫХ ТИПОВ по выборке отчёта.

    Слепок состава лежит в `census["policy"]` каждого документа (положен
    `app/core._markup_census` из sidecar сессии, момент ШИФРАЦИИ). Правило
    сведения намеренно грубое и честное:
      * все документы выборки имеют слепок И слепки совпадают -> печатаем состав;
      * иначе -> profile="mixed"/"unknown", а списки типов — null.
    Пересекать/объединять составы разных документов НЕЛЬЗЯ: получилось бы число,
    не соответствующее ни одному документу выборки, а смысл раздела ровно в том,
    чтобы читатель знал, к какому составу относятся цифры выше.
    """
    snapshots = []
    without = 0
    for d in docs:
        pol = (d.get("census") or {}).get("policy")
        if isinstance(pol, dict) and isinstance(pol.get("enabled_types"), list):
            snapshots.append((
                pol.get("profile"),
                tuple(pol.get("enabled_types") or ()),
                tuple(pol.get("disabled_types") or ()),
            ))
        else:
            without += 1

    if not snapshots:
        profile, enabled, disabled = UNKNOWN, None, None
    elif without or len(set(snapshots)) > 1:
        profile, enabled, disabled = MIXED, None, None
    else:
        profile, en, dis = snapshots[0]
        if profile not in POLICY_PROFILES:
            profile = UNKNOWN
        enabled, disabled = [_norm_type(t) for t in en], [_norm_type(t) for t in dis]

    return {
        "profile": profile,
        "enabled_types": enabled,
        "disabled_types": disabled,
        "documents_without_policy": without,
    }


def _collect_documents(scope: str, session_id: str | None) -> list[dict]:
    """[{entries: [...], census: {...}|None}] — по одному элементу на документ.

    Документы упорядочены по времени первой правки, чтобы порядковый номер
    (`document` в находках) был устойчив между двумя сборками одного отчёта.
    """
    from storage import list_markup

    if scope == "session":
        if not session_id:
            raise ValueError("Для отчёта по одному документу нужен ID сессии.")
        ids = [session_id]
    else:
        ids = _markup_session_ids()

    docs = []
    for sid in ids:
        entries = list_markup(sid)
        if not entries:
            continue
        entries = sorted(entries, key=lambda e: e.get("created_at") or "")
        # Слепок САМОЙ РАННЕЙ записи = состояние ДО ручных правок = чистый
        # результат автоматики. Позже слепок включал бы уже и ручные маски.
        census = next((e["census"] for e in entries if isinstance(e.get("census"), dict)), None)
        if census is None:
            census = _live_census(sid)
        docs.append({"entries": entries, "census": census})
    docs.sort(key=lambda d: d["entries"][0].get("created_at") or "")
    return docs


# --------------------------------------------------------------------------- #
# Метрики.
# --------------------------------------------------------------------------- #

def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 2)


def _metrics(masks_total: int | None, counts: dict) -> dict:
    """Четыре числа из знаменателя и счётчиков правок.

    found = masks_total − false_mask: маски, которые проверяющий не отклонил.
    Правки границы и типа стоят на РЕАЛЬНЫХ ПДн, поэтому из found не вычитаются —
    это качество границы/типа, а не ложное срабатывание.
    """
    if not masks_total:
        return {"precision_reviewed_pct": None, "recall_reviewed_pct": None,
                "boundary_edit_rate_pct": None, "type_error_rate_pct": None}
    # max(0, …): снятых масок МОЖЕТ оказаться больше, чем было в слепке —
    # слепок берётся с первой правки, а пользователь мог добавить ручные маски
    # и потом снять их же. Отрицательный precision («−20%») был бы бессмыслицей;
    # ноль честно говорит «из знаменателя не уцелело ничего». Расхождение при
    # этом видно по boundary_edit_rate > 100%, оно не прячется.
    found = max(0, masks_total - counts.get("false_mask", 0))
    missed = counts.get("missed", 0)
    return {
        "precision_reviewed_pct": _pct(found, masks_total),
        "recall_reviewed_pct": _pct(found, found + missed) if (found + missed) > 0 else None,
        "boundary_edit_rate_pct": _pct(counts.get("boundary", 0), masks_total),
        "type_error_rate_pct": _pct(counts.get("type", 0), masks_total),
    }


def _empty_counts() -> dict:
    return {k: 0 for k in KINDS + (UNKNOWN,)}


def _attributed_type(entry: dict, kind: str) -> str:
    """К какому типу отнести правку.

    missed — к типу, который ПОЛЬЗОВАТЕЛЬ назвал (движок не назвал никакого).
    false_mask/boundary/type — к типу, который сказал ДВИЖОК (`old_entity_type`):
    отчёт про ошибки движка, значит группировать надо по его ответу, иначе
    ошибка типа «ORG вместо PERSON» уехала бы в статистику PERSON.
    """
    if kind == "missed":
        return _norm_type(entry.get("entity_type"))
    return _norm_type(entry.get("old_entity_type") or entry.get("entity_type"))


def _attributed_detector(entry: dict, kind: str) -> str:
    if kind == "missed":
        return "none"
    return _norm_detector(entry.get("old_detector"))


# --------------------------------------------------------------------------- #
# Сборка отчёта.
# --------------------------------------------------------------------------- #

def build_report(scope: str = "all", session_id: str | None = None,
                 now: datetime | None = None) -> dict:
    """Собирает отчёт и ПРОВЕРЯЕТ его часовым перед возвратом.

    now — точка времени для `generated_at`; параметр существует ради тестов
    (два вызова с одним now обязаны дать байт-в-байт одинаковый отчёт).
    """
    if scope not in SCOPES:
        raise ValueError(f"Неизвестная область отчёта: {scope}")

    docs = _collect_documents(scope, session_id)

    totals = _empty_counts()
    masks_total = 0
    with_denominator = 0
    by_type: dict[str, dict] = {}
    by_detector: dict[str, dict] = {}
    by_build: dict[str, dict] = {}
    findings: list[dict] = []
    stamps: list[str] = []

    for doc_index, doc in enumerate(docs, start=1):
        census = doc["census"]
        if census:
            with_denominator += 1
            masks_total += int(census.get("masks_total") or 0)
            for etype, n in (census.get("by_type") or {}).items():
                bucket = by_type.setdefault(_norm_type(etype), {"masks": 0, **_empty_counts()})
                bucket["masks"] += int(n or 0)
            for det, n in (census.get("by_detector") or {}).items():
                bucket = by_detector.setdefault(_norm_detector(det), {"masks": 0, **_empty_counts()})
                bucket["masks"] += int(n or 0)

        for entry in doc["entries"]:
            kind = _norm_kind(entry.get("kind"))
            build = _norm_build(entry.get("build_mark"))
            stamp = _norm_iso(entry.get("created_at"))
            if stamp:
                stamps.append(stamp)

            totals[kind] += 1
            etype = _attributed_type(entry, kind)
            det = _attributed_detector(entry, kind)
            by_type.setdefault(etype, {"masks": 0, **_empty_counts()})[kind] += 1
            by_detector.setdefault(det, {"masks": 0, **_empty_counts()})[kind] += 1
            bbucket = by_build.setdefault(build, {**_empty_counts(), "documents_touched": 0})
            bbucket[kind] += 1

            findings.append({
                "document": doc_index,
                "kind": kind,
                "entity_type": etype,
                "detector": det,
                **_context(entry.get("segment_id")),
                **_value_shape(entry.get("value")),
                "build": build,
            })

        for build in {_norm_build(e.get("build_mark")) for e in doc["entries"]}:
            by_build.setdefault(build, {**_empty_counts(), "documents_touched": 0})["documents_touched"] += 1

    edits_total = sum(totals.values())
    report = {
        "report_version": REPORT_VERSION,
        "generated_at": (now or datetime.now().astimezone()).isoformat(),
        "tool_build": _norm_build(core.BUILD_MARK),
        "scope": scope,
        "schema_note": _SCHEMA_NOTE,
        "period": {"from": min(stamps) if stamps else None,
                   "to": max(stamps) if stamps else None},
        "contents": {"included": list(CONTENTS_INCLUDED), "excluded": list(CONTENTS_EXCLUDED)},
        "limitations": list(LIMITATIONS),
        # ЭТАП T1: состав включённых типов — БЕЗ него цифры ниже двусмысленны
        # (ноль масок типа = «не нашли» или «тип был выключен»?).
        "policy": _policy_block(docs),
        "sample": {
            "documents": len(docs),
            "documents_with_denominator": with_denominator,
            "documents_without_denominator": len(docs) - with_denominator,
            "edits": edits_total,
        },
        "totals": {"masks_total": masks_total if with_denominator else None,
                   **totals, "edits_total": edits_total},
        "metrics": _metrics(masks_total if with_denominator else None, totals),
        "by_type": {t: {**v, "edits_total": sum(v[k] for k in KINDS + (UNKNOWN,)),
                        **_metrics(v["masks"], v)}
                    for t, v in sorted(by_type.items())},
        "by_detector": {d: {**v, "edits_total": sum(v[k] for k in KINDS + (UNKNOWN,)),
                            **_metrics(v["masks"], v)}
                        for d, v in sorted(by_detector.items())},
        "by_build": {b: {**v, "edits_total": sum(v[k] for k in KINDS + (UNKNOWN,))}
                     for b, v in sorted(by_build.items())},
        "findings": findings,
    }

    assert_structural(report)
    return report


# --------------------------------------------------------------------------- #
# Структурный часовой.
# --------------------------------------------------------------------------- #

_ALLOWED_VALUES = frozenset(
    KINDS + ENTITY_TYPES + DETECTORS + CONTAINERS + CASE_SHAPES + SCOPES
    + POLICY_PROFILES + (REPORT_VERSION, UNKNOWN)
)


def _string_ok(s: str, *, as_key: bool) -> bool:
    if as_key and s in _SCHEMA_KEYS:
        return True
    if s in _ALLOWED_VALUES:
        return True
    if not as_key and s in _FIXED_PROSE:
        return True
    if _ISO_RE.match(s):
        return True
    if _BUILD_RE.match(s):
        return True
    return False


def assert_structural(report) -> None:
    """Обходит отчёт целиком и требует, чтобы КАЖДАЯ строка — ключ или значение —
    принадлежала закрытому множеству. Иначе ReportLeakError.

    Это последний рубеж: белый список полей `_FINDING_FIELDS` описывает, что мы
    СОБИРАЕМСЯ положить, а часовой проверяет, что там лежит на самом деле. Ключи
    проверяются наравне со значениями — иначе фрагмент текста мог бы приехать
    ключом словаря (например, «разбивка по значениям»)."""
    _walk(report, path="report")


def _walk(node, path: str) -> None:
    """ВАЖНО: сообщения об ошибке НЕ содержат самой отвергнутой строки — только
    путь и длину. Иначе часовой, ловящий вынос текста, сам выносил бы его в
    текст исключения, а server.py показывает это сообщение на экране и
    пользователь мог бы отправить скриншот ошибки разработчику. Для отладки
    пути и длины достаточно: они однозначно указывают на место в схеме."""
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise ReportLeakError(f"{path}: нестроковый ключ типа {type(key).__name__}")
            if not _string_ok(key, as_key=True):
                raise ReportLeakError(
                    f"{path}: ключ вне закрытого словаря отчёта "
                    f"(длина {len(key)}) — возможен вынос текста документа наружу"
                )
            _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk(item, f"{path}[{i}]")
    elif isinstance(node, str):
        if not _string_ok(node, as_key=False):
            raise ReportLeakError(
                f"{path}: строка вне закрытого словаря отчёта "
                f"(длина {len(node)}) — возможен вынос текста документа наружу"
            )
    elif isinstance(node, (int, float, bool)) or node is None:
        return
    else:
        raise ReportLeakError(f"{path}: значение неожиданного типа {type(node).__name__}")


# --------------------------------------------------------------------------- #
# Человекочитаемый вид. Рендерится ИЗ УЖЕ ПРОВЕРЕННОГО отчёта — вся проза здесь
# либо константа модуля, либо подпись из core.TYPE_LABELS, либо число.
# --------------------------------------------------------------------------- #

def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    return str(value)


def render_markdown(report: dict) -> str:
    """Тот же отчёт, читаемый человеком. Схема ОДНА (§задача 3): числа и коды
    здесь ровно те же, что в json, — просто с подписями."""
    m = report["metrics"]
    s = report["sample"]
    t = report["totals"]

    lines = [
        "# Отчёт о качестве обезличивания",
        "",
        f"- Сборка инструмента: `{report['tool_build']}`",
        f"- Отчёт собран: {report['generated_at']}",
        f"- Область: {'один документ' if report['scope'] == 'session' else 'вся накопленная разметка'}",
        f"- Период разметки: {_fmt(report['period']['from'])} — {_fmt(report['period']['to'])}",
        f"- Версия схемы отчёта: {report['report_version']}",
        "",
        "## Что в этом отчёте есть",
        "",
    ]
    lines += [f"- {x}" for x in report["contents"]["included"]]
    lines += ["", "## Чего в этом отчёте НЕТ", ""]
    lines += [f"- {x}" for x in report["contents"]["excluded"]]
    lines += ["", "## Как читать эти числа (ограничения)", ""]
    lines += [f"- {x}" for x in report["limitations"]]

    pol = report["policy"]
    lines += ["", "## Состав: какие типы маскировались", "",
              f"- Набор: {POLICY_PROFILE_LABELS.get(pol['profile'], pol['profile'])}"]
    if pol["enabled_types"] is None:
        lines += ["- Перечислить включённые типы нельзя: в выборке разные наборы "
                  "или документы без записанного состава.",
                  f"- Документов без записанного состава: {pol['documents_without_policy']}"]
    else:
        on = ", ".join(core.TYPE_LABELS.get(t, t) for t in pol["enabled_types"]) or "—"
        off = ", ".join(core.TYPE_LABELS.get(t, t) for t in pol["disabled_types"]) or "—"
        lines += [f"- Включены (маска ставится): {on}",
                  f"- Выключены (найдены, но не маскируются): {off}"]

    lines += [
        "", "## Выборка", "",
        f"- Документов в отчёте: {s['documents']}",
        f"- Из них со знаменателем (известно общее число масок): {s['documents_with_denominator']}",
        f"- Без знаменателя (сессия истекла до этапа U4): {s['documents_without_denominator']}",
        f"- Всего правок проверяющего: {s['edits']}",
        "", "## Итог", "",
        "| показатель | значение |", "|---|---:|",
        f"| Масок поставил движок | {_fmt(t['masks_total'])} |",
    ]
    lines += [f"| {KIND_LABELS[k]} | {t[k]} |" for k in KINDS]
    lines += [
        f"| правок с неизвестным видом | {t[UNKNOWN]} |",
        "",
        "| метрика | значение |", "|---|---:|",
        f"| precision по просмотренному (верхняя оценка) | {_fmt(m['precision_reviewed_pct'])}% |",
        f"| recall по замеченному (верхняя оценка) | {_fmt(m['recall_reviewed_pct'])}% |",
        f"| доля масок с поправленной границей | {_fmt(m['boundary_edit_rate_pct'])}% |",
        f"| доля масок с неверным типом | {_fmt(m['type_error_rate_pct'])}% |",
    ]

    lines += ["", "## По типам персональных данных", "",
              "| тип | масок | пропущено | ложных | границ | типов | precision | recall |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for etype, v in report["by_type"].items():
        label = core.TYPE_LABELS.get(etype, etype)
        lines.append(
            f"| {label} | {v['masks']} | {v['missed']} | {v['false_mask']} | "
            f"{v['boundary']} | {v['type']} | {_fmt(v['precision_reviewed_pct'])} | "
            f"{_fmt(v['recall_reviewed_pct'])} |"
        )

    lines += ["", "## По механизмам детекции", "",
              "| механизм | масок | пропущено | ложных | границ | типов | precision |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for det, v in report["by_detector"].items():
        lines.append(
            f"| {DETECTOR_LABELS.get(det, det)} | {v['masks']} | {v['missed']} | "
            f"{v['false_mask']} | {v['boundary']} | {v['type']} | "
            f"{_fmt(v['precision_reviewed_pct'])} |"
        )

    lines += ["", "## По версиям сборки", "",
              "| сборка | документов | пропущено | ложных | границ | типов |",
              "|---|---:|---:|---:|---:|---:|"]
    for build, v in report["by_build"].items():
        lines.append(
            f"| {build} | {v['documents_touched']} | {v['missed']} | {v['false_mask']} | "
            f"{v['boundary']} | {v['type']} |"
        )

    lines += ["", "## Находки (структурные коды, без текста)", ""]
    if not report["findings"]:
        lines.append("Правок нет — находок в отчёте тоже нет.")
    else:
        lines += ["| док | вид правки | тип | механизм | где | длина | слов | цифры | лат | кир | пункт | инициалы | регистр |",
                  "|---:|---|---|---|---|---:|---:|---|---|---|---|---|---|"]
        for f in report["findings"]:
            where = CONTAINER_LABELS.get(f["container"], f["container"])
            if f["container_index"] is not None:
                where += f" {f['container_index']}"
            if f["row"] is not None:
                where += f", строка {f['row']}, столбец {f['col']}"
            lines.append(
                f"| {f['document']} | {KIND_LABELS.get(f['kind'], f['kind'])} | "
                f"{core.TYPE_LABELS.get(f['entity_type'], f['entity_type'])} | "
                f"{DETECTOR_LABELS.get(f['detector'], f['detector'])} | {where} | "
                f"{f['length_chars']} | {f['tokens']} | {_fmt(f['has_digits'])} | "
                f"{_fmt(f['has_latin'])} | {_fmt(f['has_cyrillic'])} | {_fmt(f['has_punct'])} | "
                f"{_fmt(f['has_initials'])} | {f['case_shape']} |"
            )

    lines.append("")
    return "\n".join(lines)
