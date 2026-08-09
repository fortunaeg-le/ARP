# -*- coding: utf-8 -*-
"""ЭТАП LAYOUT — РЕМОНТНЫЙ ПРОХОД: вёрстка разрушает значение, класс целиком.

Класс (замер MEASURE-NOW): значение разорвано символом вёрстки — переносом
строки внутри токена («Кузнецо\\nва», «04\\n9205603»), типографским пробелом
внутри слова (NBSP/NNBSP: «si\\u202fb\\u200btrans»). Основной конвейер такой
текст не находит: паттерны через '\\n' не матчатся осознанно (A2-NEWLINE-CROSS),
а NBSP нормализатор сводит в ПРОБЕЛ (граница слова для NER сохраняется — иначе
склеился бы «Иванов И.И.», что проверено и отклонено), и слово остаётся рваным.

РЕШЕНИЕ — второй, ДОПОЛНИТЕЛЬНЫЙ вид текста, в котором вёрсточные разрывы
вылечены (выброшены с картой индексов — та же архитектура, что у
normalize_for_detection), и второй проход штатных детекторов по нему.
Ремонтный проход МОНОТОНЕН: он может только ДОБАВИТЬ сущности; основной вид не
меняется, и всё, что находилось раньше, находится по-прежнему (исполняемый
страж — tests/test_layout_repair.py, сравнение наборов до/после на корпусных
документах: требование владельца после урока A6, где «улучшение» метрики
оказалось потерей значения).

РАЗЛИЧЕНИЕ «мягкий разрыв vs настоящая структурная граница» (урок A2/A5):
  * лечится только разрыв ВНУТРИ ТОКЕНА — значащий символ вплотную с обеих
    сторон прогона вёрсточных символов. Настоящая граница двух значений токенов
    не рвёт; перенос по границе слова (после пробела) НЕ лечится — он неотличим
    от настоящей границы (честный остаток класса, FINDINGS);
  * NER (PERSON) — строже: только буква слева и СТРОЧНАЯ буква справа
    (сигнатура продолжения рваного слова; заглавная справа — признак нового
    структурного блока: «...работ\\nПодрядчик» не склеивается);
  * regex-типам разрешён и край из символов значения ('@', '.', '-', '_'):
    «petrov@\\ninbox.ru» — их собственный якорь/КС/фиксированная форма несут
    различение сами.

ПРИЁМ НАХОДКИ (направление ошибки — лучше не найти, чем захватить лишнее):
  1. спан обязан НАКРЫВАТЬ ремонтную позицию (лечение было необходимо — всё
     прочее дубль основного прохода);
  2. ремонтная позиция, уже накрытая сущностью основного прохода, ремонта не
     требует и проход не запускает (на чистом документе прохода нет вовсе);
  3. страж поглощения: пересечение с основной сущностью допустимо только если
     ремонтная сущность ТОГО ЖЕ ТИПА и накрывает её ЦЕЛИКОМ (расширение
     частичной находки до целого значения); любое иное пересечение — отказ;
  4. принимаются только PERSON (структурный движок) и маскируемые regex-типы;
     ORG/ADDRESS ремонтом не эмитятся (у ORG есть cross-segment E′, у ADDRESS —
     расплетающий вид; их жадность через ремонт не выпускаем).

Восстановление и координаты не трогаются: маска ложится на исходный срез
segment.text по карте индексов (spans мультиспана отображаются той же картой).
"""

import unicodedata
import uuid

from models import Entity, SourceDocument, TextSegment
from config_cache import load_yaml_cached
from normalizer import _SPACE_LIKE

#: выключатель для стража монотонности (tests monkeypatch); в бою всегда True
ENABLED = True

#: метка временного сегмента ремонтного вида — защита от рекурсии repair_pass
_REPAIR_FLAG = "_layout_repair"

#: символы «края значения» для regex-класса разрыва (email/телефон/реквизит)
_WORDLIKE_EXT = frozenset("@.-_")


def _is_cf(ch: str) -> bool:
    return unicodedata.category(ch) == "Cf"


def _scan_runs(base: str) -> list[tuple[list[int], str, str, str]]:
    """Прогоны вёрсточных символов между значащими соседями.

    Прогон — максимальная последовательность из {'\\n', типографские пробелы
    _SPACE_LIKE, невидимые Cf, обычный пробел U+0020}. Выбрасываются из него
    ТОЛЬКО '\\n' и типографские пробелы: обычный пробел — законный разделитель
    слов и НИКОГДА не выбрасывается (отклонённый приём «удалить NBSP» отклонён
    ровно за склейку слов; здесь обычные пробелы прогона выживают), Cf убирает
    сам нормализатор. Прогон без единого выбрасываемого символа — не ремонт.

    Возвращает [(drop_positions, klass, left, right)]:
      klass 'strict' — значащий сосед-alnum вплотную с обеих сторон;
      klass 'ext'    — край из _WORDLIKE_EXT (только для regex-типов).
    Прогон с иным соседом (пунктуация, край строки) ремонтом не является.
    """
    out: list[tuple[list[int], str, str, str]] = []
    n = len(base)
    i = 0
    while i < n:
        ch = base[i]
        if ch != "\n" and ch != " " and ch not in _SPACE_LIKE and not _is_cf(ch):
            i += 1
            continue
        j = i
        drops: list[int] = []
        has_space = False
        while j < n:
            cj = base[j]
            if cj == "\n" or cj in _SPACE_LIKE:
                drops.append(j)
            elif cj == " ":
                has_space = True
            elif not _is_cf(cj):
                break
            j += 1
        # Прогон с обычным пробелом лечится только ради '\n' в нём
        # («40 04 \n123456»): голый NBSP рядом с обычным пробелом — это
        # типографика границы слов, а не разрыв внутри токена.
        if has_space and all(base[p] != "\n" for p in drops):
            drops = []
        if drops:
            left = base[i - 1] if i > 0 else ""
            right = base[j] if j < n else ""
            klass = None
            if left.isalnum() and right.isalnum():
                klass = "strict"
            elif ((left.isalnum() or left in _WORDLIKE_EXT)
                  and (right.isalnum() or right in _WORDLIKE_EXT)):
                klass = "ext"
            if klass is not None:
                out.append((drops, klass, left, right))
        i = j
    return out


def _person_ok(klass: str, left: str, right: str) -> bool:
    """Разрыв, на котором разрешён приём PERSON: рваное СЛОВО со строчным
    продолжением. Заглавная справа — новый структурный блок, не продолжение."""
    return (klass == "strict" and left.isalpha()
            and right.isalpha() and right.islower())


def _maskable_types(config_path: str) -> frozenset[str]:
    """Типы с token_prefix (маскируемые). Отрицательные классы/CLAUSE_REF без
    префикса ремонтом не эмитятся."""
    config = load_yaml_cached(config_path)
    return frozenset(
        t for t, spec in config["entity_types"].items()
        if isinstance(spec, dict) and spec.get("token_prefix")
    )


def _covers(spans, p0: int, p1: int) -> bool:
    """Накрыт ли прогон [p0..p1] целиком какой-то из основных сущностей."""
    return any(s <= p0 and p1 < e for s, e in spans)


def _absorb_ok(rs: int, re_: int, etype: str, primary_here) -> bool:
    """Страж поглощения (приём 3 docstring): всякое пересечение с основной
    сущностью — только полное накрытие сущности ТОГО ЖЕ типа."""
    for ps, pe, ptype in primary_here:
        if max(rs, ps) < min(re_, pe):
            if ptype != etype or not (rs <= ps and pe <= re_) or (ps == rs and pe == re_):
                return False
    return True


def repair_pass(doc: SourceDocument, config_path: str,
                primary_entities: list[Entity]) -> list[Entity]:
    """Внутрисегментный ремонтный проход. Вызывается из pipeline.run_detection
    ПОСЛЕ основного конвейера; возвращает ТОЛЬКО дополнительные сущности."""
    if not ENABLED:
        return []
    prim_by_seg: dict[str, list[tuple[int, int, str]]] = {}
    for e in primary_entities:
        prim_by_seg.setdefault(e.segment_id, []).append((e.start, e.end, e.entity_type))

    jobs = []   # (сегмент-оригинал, rmap, [(p, klass, person_ok)])
    tmp_segments: list[TextSegment] = []
    for seg in doc.segments:
        if not seg.text or seg.metadata.get(_REPAIR_FLAG):
            continue
        det = seg.metadata.get("detection_text", seg.text)
        runs = _scan_runs(det)
        if not runs:
            continue
        prim = prim_by_seg.get(seg.id, ())
        prim_spans = [(s, e) for s, e, _t in prim]
        drop_meta: list[tuple[int, str, bool]] = []
        for drops, klass, left, right in runs:
            if _covers(prim_spans, drops[0], drops[-1]):
                continue   # значение уже найдено основным проходом — не лечим
            ok_per = _person_ok(klass, left, right)
            for p in drops:
                drop_meta.append((p, klass, ok_per))
        if not drop_meta:
            continue
        dropset = {p for p, _k, _o in drop_meta}
        rmap = [i for i in range(len(seg.text)) if i not in dropset]
        rtext = "".join(seg.text[i] for i in rmap)
        rdet = "".join(det[i] for i in rmap)
        # Служебные ключи (с подчёркивания) — кэши видов с картами индексов
        # ИСХОДНОГО текста (_norm_cache/_addr_view_cache/...): в ремонтный
        # сегмент им нельзя — карты не от его текста.
        metadata = {k: v for k, v in seg.metadata.items()
                    if k != "detection_text" and not k.startswith("_")}
        metadata[_REPAIR_FLAG] = True
        if rdet != rtext:
            metadata["detection_text"] = rdet
        tmp_segments.append(TextSegment(
            id=seg.id, text=rtext, source_type=seg.source_type, metadata=metadata,
        ))
        jobs.append((seg, rmap, drop_meta))

    if not jobs:
        return []

    from pipeline import run_detection
    tmp_doc = SourceDocument(
        segments=tmp_segments, source_format=doc.source_format,
        source_path=doc.source_path,
    )
    found = run_detection(tmp_doc, config_path)

    by_seg: dict[str, list[Entity]] = {}
    for e in found:
        by_seg.setdefault(e.segment_id, []).append(e)

    maskable = _maskable_types(config_path)
    out: list[Entity] = []
    for seg, rmap, drop_meta in jobs:
        prim_here = prim_by_seg.get(seg.id, ())
        for e in by_seg.get(seg.id, ()):
            if e.end <= e.start or e.end > len(rmap):
                continue
            rs, re_ = rmap[e.start], rmap[e.end - 1] + 1
            inside = [(p, ok) for p, _k, ok in drop_meta if rs < p < re_]
            if not inside:
                continue   # лечение не потребовалось — дубль основного прохода
            if e.entity_type == "PERSON":
                if not any(ok for _p, ok in inside):
                    continue
            elif e.detector != "regex" or e.entity_type not in maskable:
                continue   # ORG/ADDRESS/отрицательные классы — не через ремонт
            if not _absorb_ok(rs, re_, e.entity_type, prim_here):
                continue
            spans = None
            if e.spans:
                spans = [(rmap[s], rmap[ee - 1] + 1) for s, ee in e.spans]
            out.append(Entity(
                id=str(uuid.uuid4()), segment_id=seg.id,
                start=rs, end=re_, original_text=seg.text[rs:re_],
                entity_type=e.entity_type, detector=e.detector,
                confidence=e.confidence, spans=spans,
                group_key=e.group_key, canonical=e.canonical,
            ))
    return out


# --------------------------------------------------------------------------- #
#                     РЕМОНТ СТЫКА ГРАНИЧНОГО ОКНА B3                          #
# --------------------------------------------------------------------------- #

def seam_eligibility(window: str, tail_end: int, head_start: int) -> tuple[bool, bool]:
    """(regex_ok, person_ok) для стыка окна B3.

    Соседи стыка — ближайшие не-Cf символы хвоста/головы ВПЛОТНУЮ к шву
    (пробел режет право на лечение токена, но не право regex: у паттерна
    собственная структура, а спан обязан пересечь стык и пройти страж
    поглощения — стопка двух целых значений не сшивается, каждая сторона
    уже цела и накрыта посегментным проходом)."""
    i = tail_end - 1
    while i >= 0 and _is_cf(window[i]):
        i -= 1
    j = head_start
    while j < len(window) and _is_cf(window[j]):
        j += 1
    left = window[i] if i >= 0 else ""
    right = window[j] if j < len(window) else ""
    person = _person_ok("strict", left, right) if (
        left.isalnum() and right.isalnum()) else False
    return True, person
