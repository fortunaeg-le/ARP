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

import os
import unicodedata
import uuid

from models import Entity, SourceDocument, TextSegment
from config_cache import load_yaml_cached
from normalizer import _SPACE_LIKE

#: выключатель для стража монотонности (tests monkeypatch) и разведочных
#: замеров до/после (переменная окружения доходит до воркеров Pool, в отличие
#: от monkeypatch). В бою переменная не выставляется — всегда True.
ENABLED = os.environ.get("SHIFRATOR_LAYOUT_REPAIR", "1") != "0"

#: метка временного сегмента ремонтного вида — защита от рекурсии repair_pass
_REPAIR_FLAG = "_layout_repair"

#: символы «края значения» для regex-класса разрыва (email/телефон/реквизит).
#: '_' НАМЕРЕННО не входит: подчёркивание рядом с разрывом — это линия подписи
#: «____», и '_' внутри \w дотягивал домен email до неё (вскрыто подвыборкой).
#:
#: ЭТАП SEAM-JOIN — СКОБКИ. Код города телефона пишется в скобках, и разрыв
#: садится ровно на них: «+7 (495) \n981-19-41», «8(843\n)4341367», «+7 (\n383)».
#: Без скобок в этом своде прогон не классифицировался ВООБЩЕ и ремонт по нему
#: не запускался — 13 телефонов класса вёрстки. Скобка — структура значения
#: (как '@' у почты), и она же ДОКАЗЫВАЕТ тип сама: собранное со скобкой
#: значение не «голая цифровая цепь», страж A6 якоря с него не требует, а
#: глухие склейки прозы («работ\n(Подрядчик)») ремонт эмитить не может —
#: PERSON на классе 'ext' не принимается, а regex там нечего матчить.
_WORDLIKE_EXT = frozenset("@.-()")


def _is_cf(ch: str) -> bool:
    return unicodedata.category(ch) == "Cf"


#: «жёсткий» разрыв — граница ПО САМОЙ РАЗМЕТКЕ, а не типографика набора:
#: перенос строки и табуляция (граница ячейки внутри абзаца). Оба лечатся
#: только внутри одного токена и только по одному в прогоне.
_HARD_BREAK = frozenset("\n\t")

#: чем ограничен ТОКЕН при разборе сторон разрыва: пробельное и вёрсточное.
#: Знаки препинания токен НЕ рвут — «704-068,» и «д.63» это один токен записи.
_TOKEN_BREAK = frozenset("\n\r\t ") | _SPACE_LIKE

#: символы, допустимые внутри ЦИФРОВОГО токена помимо цифр и их омоглифов
#: (_DIGIT_FOLD ниже): разделители групп записи реквизита и номера.
_NUM_TOKEN_EXTRA = frozenset("-–—‑‒−/№.,()+№")


def _side_token(base: str, i: int, j: int) -> tuple[str, str]:
    """Токены вплотную слева и справа от прогона [i, j)."""
    a = i
    while a > 0 and base[a - 1] not in _TOKEN_BREAK:
        a -= 1
    b = j
    while b < len(base) and base[b] not in _TOKEN_BREAK:
        b += 1
    return base[a:i], base[j:b]


def _is_numeric_token(tok: str) -> bool:
    """Токен ЗАПИСИ ЧИСЛА: хотя бы одна настоящая цифра и ни одной буквы,
    кроме омоглифов цифр. «149ЗІ1О09843б» — число, «Иваново» — нет."""
    core = [ch for ch in tok if not _is_cf(ch)]
    if not any(ch.isdigit() for ch in core):
        return False
    return all(ch.isdigit() or ch in _DIGIT_FOLD or ch in _NUM_TOKEN_EXTRA
               for ch in core)


def _same_token(base: str, i: int, j: int) -> bool:
    """Один ли ТОКЕН по обе стороны прогона [i, j) — то есть разрыв ВНУТРИ
    значения, а не структурная граница двух полей.

    ЭТАП SEAM-JOIN, различение для прогонов с '\\n' (докстринг модуля, правило
    «лечится только разрыв ВНУТРИ ТОКЕНА» — раньше оно проверялось по ОДНОМУ
    символу с каждой стороны, и «…525187\\nТел.:» проходило как внутритокенное):

      * число | число — разорванная запись числа («044\\n525187», «149З\\nІ1О09843б»);
      * слово | слово — разорванное слово («Морозо\\nва», «ИН\\nН»), КРОМЕ шага
        регистра вверх: строчная слева и ЗАГЛАВНАЯ справа — начало нового
        структурного блока («Олеговна\\nАдрес регистрации»), тот же признак, на
        котором стоит _person_ok;
      * число | слово и слово | число — две РАЗНЫЕ записи («525187\\nТел.»,
        «Иваново\\n2024»): значение кончилось, началась метка.

    Омоглиф цифры считается цифрой (свод normalizer его ещё не применял к этому
    виду), поэтому испорченный реквизит остаётся числом по обе стороны."""
    left, right = _side_token(base, i, j)
    if not left or not right:
        return False
    if _is_numeric_token(left) and _is_numeric_token(right):
        return True
    lc = [ch for ch in left if not _is_cf(ch)]
    rc = [ch for ch in right if not _is_cf(ch)]
    if not lc or not rc or not lc[-1].isalpha() or not rc[0].isalpha():
        return False
    if _is_numeric_token(left) or _is_numeric_token(right):
        return False
    return not (lc[-1].islower() and rc[0].isupper())


def _scan_runs(base: str) -> list[tuple[list[int], str, str, str]]:
    """Прогоны вёрсточных символов между значащими соседями.

    Прогон — максимальная последовательность из {'\\n', ТАБУЛЯЦИЯ, типографские
    пробелы _SPACE_LIKE, невидимые Cf, обычный пробел U+0020}. Выбрасываются из
    него ТОЛЬКО '\\n', табуляция и типографские пробелы: обычный пробел —
    законный разделитель слов и НИКОГДА не выбрасывается (отклонённый приём
    «удалить NBSP» отклонён ровно за склейку слов; здесь обычные пробелы
    прогона выживают), Cf убирает сам нормализатор. Прогон без единого
    выбрасываемого символа — не ремонт.

    ЭТАП SEAM-JOIN — ТАБУЛЯЦИЯ. Граница ячейки внутри абзаца приходит из
    разметки табуляцией, и класс `mut:cell_split` до этого этапа не лечился
    ВООБЩЕ: «Макаро\\tв Юрий Николаевич», «С. \\tВ. Максимова» — 10 из 14
    вхождений класса утекали (71.4 %, самая тяжёлая доля во всём корпусе).
    Табуляция — такая же граница по разметке, как '\\n', поэтому она проходит
    через ТЕ ЖЕ два ограничителя ниже: пара «жёстких» разрывов в одном прогоне
    структурна, а склейка двух разных токенов запрещена (_same_token). В .txt
    табуляция разделяет колонки — именно эти два правила и не дают склеить
    «ИНН\\t7707083893» (число справа, слово слева) в один токен.

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
        if (ch not in _HARD_BREAK and ch != " "
                and ch not in _SPACE_LIKE and not _is_cf(ch)):
            i += 1
            continue
        j = i
        drops: list[int] = []
        has_space = False
        while j < n:
            cj = base[j]
            if cj in _HARD_BREAK or cj in _SPACE_LIKE:
                drops.append(j)
            elif cj == " ":
                has_space = True
            elif not _is_cf(cj):
                break
            j += 1
        # Прогон с обычным пробелом лечится только ради жёсткого разрыва в нём
        # («40 04 \n123456»): голый NBSP рядом с обычным пробелом — это
        # типографика границы слов, а не разрыв внутри токена.
        if has_space and all(base[p] not in _HARD_BREAK for p in drops):
            drops = []
        # ДВА и более жёстких разрыва в прогоне — пустая строка или пропуск
        # ячейки, настоящая граница по самой разметке. Не лечится никогда:
        # мягкий разрыв внутри значения — это ОДИН разрыв (вскрыто подвыборкой:
        # '\n\n' перед линией подписи склеивал домен email с «____»).
        if sum(1 for p in drops if base[p] in _HARD_BREAK) >= 2:
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
            # ЭТАП SEAM-JOIN. Прогон, который СКЛЕИВАЕТ два токена переносом
            # строки (перенос есть, обычного пробела в прогоне не осталось),
            # лечится только если по обе стороны ОДИН И ТОТ ЖЕ токен
            # (_same_token). '\n' — граница по самой разметке; склейка двух
            # РАЗНЫХ токенов через неё не чинит ничего, а ломает соседей:
            # «БИК 044\n525187\nТел.:» превращалось в «БИК 044525187Тел.:»,
            # где `\b` паттерна BIK уже не срабатывает, — значение терялось
            # ровно из-за ремонта (16 БИК корпуса, разведка
            # experiments/seam_join_why.py).
            #
            # Правило узкое НАМЕРЕННО, три оговорки:
            #   * прогон с ВЫЖИВШИМ обычным пробелом («199 898,00\n руб.»)
            #     ничего не склеивает — там правило не применяется;
            #   * типографский пробел без '\n' — артефакт набора, а не граница
            #     разметки, и тоже не проверяется;
            #   * класс 'ext' (край '@'/'.'/'-') внутри значения по построению
            #     («petrov@\nbk.ru»): токены там кончаются на служебном символе,
            #     и требовать от них однородности значило бы отменить ремонт
            #     почты целиком (замерено: −11 EMAIL).
            if (klass == "strict" and not has_space
                    and any(base[p] in _HARD_BREAK for p in drops)
                    and not _same_token(base, i, j)):
                klass = None
            if klass is not None:
                out.append((drops, klass, left, right))
        i = j
    out.extend(_spread_runs(base, {p for drops, _k, _l, _r in out for p in drops}))
    out.sort(key=lambda r: r[0][0])
    return out


#: сколько односимвольных токенов подряд считать РАЗРЯДКОЙ. Три — это ещё
#: обычная речь («срок и в порядке» даёт два подряд), четыре подряд в русском
#: тексте не встречаются: проверено прогоном по всем 324 документам корпуса v1
#: — таких цепочек там НОЛЬ, ни одной ложной и ни одной настоящей.
_SPREAD_MIN = 4


def _spread_runs(base: str, taken: set):
    """РАЗРЯДКА внутри значения: «И в а н о в», «П Е Т Р О В».

    ЭТАП SEAM-JOIN, третий вид разрыва вёрстки. Обычный пробел этот модуль не
    выбрасывает НИКОГДА (докстринг _scan_runs: пробел — законный разделитель
    слов), и одного этого правила хватало, пока разрыв был одиночным. Разрядка
    ломает его формой: слово набрано по букве через пробел, и «разделитель
    слов» здесь не разделяет ничего.

    Признак — только форма записи, без словарей: ЧЕТЫРЕ и более односимвольных
    БУКВЕННЫХ токена подряд, разделённых ровно одним пробелом. Три подряд ещё
    бывают в речи, четыре — нет (см. _SPREAD_MIN).

    ЦИФРЫ СЮДА НЕ ВХОДЯТ, и это решение, а не забывчивость: разрядка цифр —
    это разрядная группировка числа, у неё своя форма записи и свой разбор в
    normalizer (этап CHAR-NORM, «12 000 000» не трогается, «90 14 6170 85»
    схлопывается). Продублировать её здесь значило бы поставить второе правило
    на тот же текст — и «1 2 3 4» соседних колонок склеилось бы в «1234».
    """
    out = []
    n = len(base)
    i = 0
    while i < n:
        if not (base[i].isalpha() and (i == 0 or base[i - 1] in _TOKEN_BREAK)):
            i += 1
            continue
        gaps = []
        j = i
        while (j + 2 < n and base[j].isalpha() and base[j + 1] == " "
               and base[j + 2].isalpha()
               and (j + 3 >= n or base[j + 3] in _TOKEN_BREAK)):
            gaps.append(j + 1)
            j += 2
        if len(gaps) + 1 >= _SPREAD_MIN:
            for p in gaps:
                if p in taken:
                    continue
                out.append(([p], "spread", base[p - 1], base[p + 1]))
        i = max(j + 1, i + 1)
    return out


def _person_ok(klass: str, left: str, right: str) -> bool:
    """Разрыв, на котором разрешён приём PERSON: рваное СЛОВО со строчным
    продолжением. Заглавная справа — новый структурный блок, не продолжение.

    ЭТАП SEAM-JOIN: у разрядки («П Е Т Р О В А Мария») правило регистра снято.
    Оно и заводилось как ЕДИНСТВЕННЫЙ различитель «продолжение слова vs новый
    блок» там, где разрыв одиночный и другой опоры нет; у разрядки опора есть
    в самой форме — четыре и более односимвольных токена подряд, — и требовать
    вдобавок строчную справа значило бы отсечь ровно сплошь-заглавную запись
    фамилии, ради которой приём и нужен."""
    if klass == "spread":
        return left.isalpha() and right.isalpha()
    if klass != "strict" or not left.isalpha() or not right.isalpha():
        return False
    # ЭТАП SEAM-JOIN: запрещён ШАГ РЕГИСТРА ВВЕРХ, а не заглавная сама по себе.
    # Признак «новый блок» несёт именно шаг «строчная -> ЗАГЛАВНАЯ»
    # («…работ\nПодрядчик», «…Олеговна\nАдрес»); когда заглавные с ОБЕИХ сторон,
    # шага нет и признака нет — это сплошь-заглавная запись, разорванная внутри
    # слова («ИВАНО\nВ ЕГОР ОЛЕГОВИЧ»), которую прежняя редакция правила теряла
    # целиком. То же различение, что у _same_token, теперь и здесь.
    return not (left.islower() and right.isupper())


def _maskable_types(config_path: str) -> frozenset[str]:
    """Типы с token_prefix (маскируемые). Отрицательные классы/CLAUSE_REF без
    префикса ремонтом не эмитятся."""
    config = load_yaml_cached(config_path)
    return frozenset(
        t for t, spec in config["entity_types"].items()
        if isinstance(spec, dict) and spec.get("token_prefix")
    )


def _covers(spans, p0: int, p1: int) -> bool:
    """Накрыт ли прогон [p0..p1] целиком какой-то из основных сущностей.

    ЭТАП SEAM-JOIN: правило оставлено КАК БЫЛО, и это результат замера, а не
    недосмотр. Долг `DATE-REPAIR-BLOCKS-TERM` называет виновником именно его
    («_covers считает накрытым прогон, накрытый ЛЮБОЙ сущностью»). Проверено
    прямым отключением на обоих корпусах: на v1 (47 документов класса вёрстки)
    отключение даёт −1 маску и НОЛЬ снятых утечек, на v2 (138 документов) —
    побайтно тот же отчёт, TERM 98.3 % и там, и там. Названный механизм на
    сегодняшнем коде НЕ воспроизводится; трогать правило без своей причины —
    менять поведение вслепую."""
    return any(s <= p0 and p1 < e for s, e in spans)


def _absorb_ok(rs: int, re_: int, etype: str, primary_here,
               allow_absorb: bool) -> bool:
    """Страж поглощения (приём 3 docstring).

    PERSON (allow_absorb=True): пересечение с основной сущностью допустимо
    только как полное накрытие сущности ТОГО ЖЕ типа — частичная находка NER
    расширяется до целого значения, это норма механизма.

    Regex-типы (allow_absorb=False): ЛЮБОЕ пересечение — отказ. Матч паттерна —
    всегда ЦЕЛОЕ значение; если по одну сторону разрыва значение уже найдено
    целиком, разрыв не внутри значения, а между значением и соседним словом, и
    «расширение» — это захват этикетки («Почта\\nvolkov@…» → EMAIL забирал
    «Почта»: класс перебора границ, вскрытый подвыборкой)."""
    for ps, pe, ptype in primary_here:
        if max(rs, ps) < min(re_, pe):
            if not allow_absorb:
                return False
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
        tmp_seg = TextSegment(
            id=seg.id, text=rtext, source_type=seg.source_type, metadata=metadata,
        )
        tmp_segments.append(tmp_seg)
        jobs.append((seg, rmap, drop_meta))

    if not jobs:
        return []

    tmp_by_id = {s.id: s for s in tmp_segments}
    from pipeline import run_detection
    tmp_doc = SourceDocument(
        segments=tmp_segments, source_format=doc.source_format,
        source_path=doc.source_path,
    )
    # skip_address: ниже (блок эмита) адресные сущности отбрасываются безусловно —
    # «ORG/ADDRESS через ремонт не эмитятся». Считать их незачем, а стоят они 33%
    # времени детекции (ЭТАП DEBT-1, профиль). Эквивалентность — docstring detect_ner.
    found = run_detection(tmp_doc, config_path, skip_address=True)

    by_seg: dict[str, list[Entity]] = {}
    for e in found:
        by_seg.setdefault(e.segment_id, []).append(e)
        # ЭТАП SEAM-JOIN. Составная «ИП + ФИО» склеивается в ORG уже ВНУТРИ
        # ремонтного прохода (merge_compound_entities — часть run_detection), и
        # приём 4 («ORG ремонтом не эмитим») выбрасывал её вместе с человеком:
        # «ИП Поп\nов Павел Петрович» не находил никто, хотя залеченное имя
        # движок видел. Поглощённое ФИО составная несёт в `shadowed` (этап
        # DEFAULT-GATE, MFIX-PROFILE-IP-PER) — берём ЕГО, а не саму ORG:
        # запрет на ORG остаётся в силе, а человек получает свою маску.
        if e.entity_type == "ORG":
            for sh in (e.shadowed or ()):
                if sh.entity_type == "PERSON" and sh.segment_id == e.segment_id:
                    by_seg.setdefault(e.segment_id, []).append(sh)

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
            elif any(seg.text[p] == "\n" for p, _ok in inside):
                # значение СОБРАНО через перенос строки — строгий страж A6
                rdet = tmp_by_id[seg.id].metadata.get(
                    "detection_text", tmp_by_id[seg.id].text)
                if not _strict_seam_ok(tmp_by_id[seg.id].text[e.start:e.end],
                                       e.entity_type, rdet, e.start, config_path):
                    continue
            if not _absorb_ok(rs, re_, e.entity_type, prim_here,
                              allow_absorb=(e.entity_type == "PERSON")):
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
#            СТРОГИЙ СТРАЖ СБОРКИ ЧЕРЕЗ '\n' (правило A6 для ремонта)          #
# --------------------------------------------------------------------------- #
# Посегментная детекция под якорем маскирует значение и с несошедшейся КС
# (опечатка в ПДн — всё равно ПДн, regex_detector этап 4). СБОРКА через разрыв
# строже: она конструирует значение, которого в тексте не было, и обязана
# доказать его сама — КС, где определена, сходится ВСЕГДА (тест
# test_broken_checksum_is_not_stitched), а голая цифровая цепь без якоря типа
# не принимается вовсе (две ячейки соседних строк «770708»/«3893» — это не ИНН,
# тест test_boundary_does_not_stitch_across_pipe_separated_prose).

#: зеркало multispan._LOOKALIKE — счёт эффективных цифр значения
_DIGIT_FOLD = {"О": "0", "о": "0", "O": "0", "o": "0", "З": "3", "з": "3",
               "Ч": "4", "ч": "4", "І": "1", "і": "1", "б": "6"}

#: символы «голой цифровой цепи»: цифры/омоглифы цифр, пробельные, дефисы.
#: Скобки/плюс/точки — уже СТРУКТУРА значения (телефон, дата): их формы
#: доказывают тип сами, на них правило якоря не распространяется.
_BARE_EXTRA = frozenset(" \t\n\r-–—‑‒−")


#: кэш скомпилированных якорей типа для стража сборки (страж зовётся на каждое
#: граничное окно документа — компилировать заново нельзя).
_SEAM_ANCHOR_CACHE: dict[tuple[str, str], tuple] = {}


def _seam_anchors(etype: str, spec: dict, config_path: str) -> tuple:
    """ВСЕ якоря типа для стража сборки, взятые ИЗ КОНФИГА.

    ЭТАП SEAM-JOIN. Раньше страж смотрел ровно в одно место — ключ `anchor:`
    типа, а при его отсутствии в рукописное зеркало
    `multispan._LOCAL_SEAM_ANCHORS`. Тип с НЕСКОЛЬКИМИ формами значения держит
    якорь ВНУТРИ элемента `patterns[]` (свой на каждую форму), и до этого якоря
    страж не доставал вовсе:

      * `KPP` — якорь `(?i)кпп` лежит в `patterns[0].anchor`, ключа `anchor:` у
        типа нет; голая девятизначная цепь, собранная через стык, не
        принималась НИКОГДА (5 отказов на документах класса вёрстки);
      * `PASSPORT` — вторая форма («код подразделения 704-068») несёт якорь
        внутри самого паттерна, а зеркало знало голову только ПЕРВОЙ формы
        («паспорт|серия»). 57 отказов на 47 документах класса — крупнейший
        единичный блокиратор класса вёрстки (разведка
        `experiments/seam_join_seamlog.py`).

    Порядок: ключ `anchor:` типа -> якоря элементов `patterns[]` -> рукописное
    зеркало голов паттернов (там, где якорь вплавлен в regex и отдельным ключом
    не выражен). Годится ЛЮБОЙ — это то же требование «якорь типа слева»,
    просто перечисленное полностью, а не одной случайной веткой."""
    import re as _re
    key = (config_path, etype)
    cached = _SEAM_ANCHOR_CACHE.get(key)
    if cached is not None:
        return cached
    from multispan import _LOCAL_SEAM_ANCHORS
    out = []
    if spec.get("anchor"):
        out.append(_re.compile(spec["anchor"]))
    for p in spec.get("patterns") or ():
        if isinstance(p, dict) and p.get("anchor"):
            out.append(_re.compile(p["anchor"]))
    mirror = _LOCAL_SEAM_ANCHORS.get(etype)
    if mirror is not None:
        out.append(mirror)
    res = tuple(out)
    _SEAM_ANCHOR_CACHE[key] = res
    return res


def _strict_seam_ok(matched: str, etype: str, ctx: str, start: int,
                    config_path: str) -> bool:
    """Пропускает regex-находку, СОБРАННУЮ через разрыв, по правилу A6."""
    from regex_detector import VALIDATORS, _has_anchor, _fold_anchor_context
    spec = load_yaml_cached(config_path)["entity_types"].get(etype) or {}
    validator = VALIDATORS.get(spec.get("validate") or "")
    if validator is not None:
        digits = "".join(_DIGIT_FOLD.get(ch, ch) for ch in matched
                         if ch.isdigit() or ch in _DIGIT_FOLD)
        if not validator(digits):
            return False
    bare = all(ch.isdigit() or ch in _DIGIT_FOLD or ch in _BARE_EXTRA
               for ch in matched)
    if bare:
        anchors = _seam_anchors(etype, spec, config_path)
        if not anchors:
            return False
        folded = _fold_anchor_context(ctx)
        if not any(_has_anchor(folded, start, a) for a in anchors):
            return False
    return True


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


#: края ЛЕВОГО КОНТЕКСТА стыка — свод краёв значения плюс косая черта: якорь
#: счёта «р/с» рвётся ровно по ней. В _WORDLIKE_EXT косую не вносим — там она
#: дала бы право на спан, а здесь речь только об улике для якоря.
_CTX_EDGE = _WORDLIKE_EXT | frozenset("/")


def seam_joins_word(left_text: str, right_text: str) -> bool:
    """Рвёт ли стык двух соседних сегментов ОДНО СЛОВО (не значение).

    ЭТАП SEAM-JOIN, левый контекст стыка. Вёрстка рвёт не только значение, но и
    ЯКОРЬ рядом с ним, причём другим стыком: «…900000000790 БИ | К 0440307 | 90»
    — три сегмента, два разрыва. Окно B3 видит ОДИН стык, поэтому при сборке
    «044030790» слева от значения оказывается «К », слова «БИК» в окне нет
    вовсе, и штатный паттерн BIK (у него якорь обязателен) не срабатывает.
    Тот же случай у «р/ | с 40802810205 | 101445449» и «…Паспо | рт 90 14 617 |
    085».

    Признак ровно тот же, что у внутрисегментного разрыва (_same_token), и
    сознательно УЖЕ него: только СЛОВО, не число. Приклеивать левый контекст
    через разрыв ЧИСЛА нельзя — «…790 | 044030790» слились бы в одну цепь и
    дали бы значение, которого в документе нет."""
    if not left_text or not right_text:
        return False
    base = left_text + "\n" + right_text
    i = len(left_text)
    left, right = base[i - 1], base[i + 1]
    if not right.isalnum():
        return False
    if _is_numeric_token(_side_token(base, i, i + 1)[0]):
        return False
    if left.isalnum():
        return _same_token(base, i, i + 1)
    # Служебный символ на краю: «р/ | с 40802810205» — якорь счёта «р/с» рвётся
    # ровно по косой черте. Косая в свод краёв ЗНАЧЕНИЯ (_WORDLIKE_EXT) не
    # входит и не должна: там она дала бы право на СПАН. Здесь речь только о
    # приклеенном контексте, то есть об улике для якоря, — территорию он не
    # получает ни при каком краю.
    return left in _CTX_EDGE
