"""Блок 3 — NER-детектор.

Находит PERSON/ORG через стандартный NER-пайплайн Natasha и почтовые адреса
(ADDRESS) через natasha.AddrExtractor. Публичная функция — detect_ner.

Модели Natasha инициализируются один раз при импорте модуля (это секунды и
сотни МБ памяти — см. HANDOFF_3, раздел "Побочные эффекты импорта").
"""

import os
import re
import sys
import uuid

# Этап E'' (детерминизм детекции). НЕ вокруг конкретного найденного бага (репро на
# синтетике не подтвердило недетерминизм — см. HANDOFF), а защитная мера от КЛАССА
# дефекта: NewsEmbedding/NewsNERTagger используют numpy, а numpy на этой машине
# собран с OpenBLAS (DYNAMIC_ARCH, до 24 потоков). Многопоточный BLAS может давать
# разный порядок float-редукций в зависимости от того, сколько потоков реально
# участвовало в конкретном вызове (планировщик ОС, разогрев пула) — на порогах
# уверенности NER это теоретически способно перекинуть решение туда-сюда между
# вызовами. Фиксируем ОДИН поток ДО первого импорта natasha (numpy читает эти
# переменные при инициализации BLAS-библиотеки, значит выставлять нужно раньше
# `from natasha import ...`). `setdefault` — не перебивает явную настройку окружения,
# если владелец процесса сам задал число потоков.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import yaml

from models import Entity, SourceDocument
from normalizer import detection_view, norm_to_src, src_to_norm
from natasha import (
    Segmenter,
    MorphVocab,
    NewsEmbedding,
    NewsNERTagger,
    AddrExtractor,
    Doc,
)

# --- Инициализация моделей один раз при старте модуля ---
_segmenter = Segmenter()
_morph_vocab = MorphVocab()
_emb = NewsEmbedding()
_ner_tagger = NewsNERTagger(_emb)
_addr_extractor = AddrExtractor(_morph_vocab)

# Прогрев: первый вызов tag_ner лениво довычисляет/дозагружает внутренности модели
# (см. HANDOFF_STAGE_EPRIME_DETERMINISM) — если бы "холодный" первый вызов отличался
# от прогретых последующих, различие досталось бы ПЕРВОМУ документу процесса, а не
# фиктивному тексту. Прогреваем здесь, при импорте модуля, а не на первом реальном
# документе пользователя.
_warmup_doc = Doc("прогрев модели")
_warmup_doc.segment(_segmenter)
_warmup_doc.tag_ner(_ner_tagger)
del _warmup_doc

# Расширение PERSON-спана инициалами вида "И.И." справа/слева от спана.
# Паттерны из ТЗ применяются к срезам text[end:] / text[:start], поэтому якоря
# ^ и $ работают на границе среза (а не на реальном начале/конце сегмента).
_INITIALS_RIGHT = re.compile(r"^[  ]?[А-ЯЁ]\.[  ]?[А-ЯЁ]\.")
_INITIALS_LEFT = re.compile(r"[А-ЯЁ]\.[  ]?[А-ЯЁ]\.[  ]?$")

# Символы, из которых (и только из которых) может состоять разрыв между
# двумя соседними Match'ами AddrExtractor, чтобы их склеить в один Entity.
_ADDR_GAP_CHARS = frozenset(" ,. ")
_ADDR_GAP_MAXLEN = 6

# --- Гибридная детекция адреса (этап A волны 2): место — контекстно, спан — полный ---
#
# Раньше AddrExtractor (yargy) гонялся СКАНЕРОМ по тексту КАЖДОГО сегмента (~95%
# времени детекции, см. docs/reports/RECON_REPORT.md) и всё равно ронял хвост адреса
# (дом/индекс) в ~2/3 случаев. Теперь yargy — ПАРСЕР окрестности, а не сканер:
#   1. Место адреса детектируем контекстно: LOC-спаны NER-тэггера (бессловарно) +
#      regex-триггер на сильные адресные маркеры — для адресов с НЕЗНАКОМЫМ топонимом,
#      которые LOC не знает («д. Простоквашино»). Маркеры — ДОПОЛНЕНИЕ к LOC (объединение),
#      а НЕ единственный механизм: сам адрес подтверждают LOC и/или yargy, маркер лишь
#      решает «здесь есть место → стоит запустить парсер».
#   2. yargy запускаем ТОЛЬКО на сегментах с хитом. Сложность O(весь текст)→O(хиты).
#   3. Итоговый спан = объединение LOC- и yargy-спанов, кластеризованное по близости
#      (склеивает разорванные грамматикой куски вроде «…наб. реки Фонтанки…») и
#      КОНСЕРВАТИВНО расширенное по краям адресными токенами (индекс/тип-улицы/дом/кв),
#      НИКОГДА не сужаемое. Асимметрия цены: лишнее закрытое слово — шум, незакрытый
#      хвост адреса — утечка ПДн; при сомнении закрываем БОЛЬШЕ.

# Сильные адресные маркеры МЕСТА — ТРИГГЕР запуска yargy на сегменте (в дополнение к LOC).
# Намеренно БЕЗ одиночных дом/кв («д. 5», «кв. 12»): они частотны в юр-ссылках и
# анкорили бы парсер на неадресных сегментах. Намеренно БЕЗ голого почтового индекса
# (\\b\\d{6}\\b): в корпусе он ВСЕГДА соседствует с городом (то есть уже покрыт LOC/
# маркером места), а как самостоятельный анкор ловил паспортный хвост «…паспорт 4509
# 123456» и через yargy-ошибку на ФИО давал ложный ADDRESS. Анкор — это МЕСТО
# (город/тип-поселения/улица/регион); «д.\\s*<буква>» ловит деревню-топоним, не «д. <цифра>».
_ADDR_ANCHOR_RE = re.compile(
    r"(?i)("
    r"\bг\.\s*[а-яё]|\bгор\.\s*[а-яё]|\bгород\s+[а-яё]"              # город
    r"|\bд\.\s*[а-яё]|\b(?:пос|пгт|рп|дер|деревня|село|ст-ца|станица|хутор|мкр|мкр-н|мкрн)\b"  # поселение
    r"|\b(?:обл\.|область|край|респ\.|республика|р-н|район|округ)\b"  # регион
    r"|\b(?:ул\.|улица|пр-т|пр-кт|проспект|пер\.|переулок|наб\.|набережная"
    r"|ш\.|шоссе|б-р|бульвар|проезд|линия|тупик|аллея|пл\.|площадь|квартал)\b"  # улица
    r")"
)

# Кластеризация: два адресных куска склеиваются в один, если разрыв между ними не
# длиннее — закрывает многословные названия улиц, которые грамматика yargy рвёт на
# части («г. СПб» ‖ «наб. реки Фонтанки» ‖ «д. 15»).
# ЭТАП C: БОЛЕЕ ШИРОКИЙ триггер ЗАПУСКА yargy на сегменте (в дополнение к LOC и
# _ADDR_ANCHOR_RE). Раньше сегмент без LOC-хита и без маркера НЕ отдавался yargy —
# адреса «А/Я 27, Г. ОДИНЦОВО, 143000» (ALL-CAPS город, LOC молчит) и «г. Казань,
# 420111» (город + индекс, _ADDR_ANCHOR_RE требует СТРОЧНУЮ после «г.») не сеялись.
# Теперь строгий ЯКОРНЫЙ гейт (_addr_span_strong) фильтрует ложные раскрытия, поэтому
# сеять можно шире без роста FP: а/я, «г./город» ЛЮБОГО регистра, 6-значный индекс.
_ADDR_SEED_RE = re.compile(
    r"(?i)(?:а\s*/\s*я\s*\d"
    r"|\bг(?:ор)?\.\s*[а-яёa-z]|\bгород\s+[а-яёa-z]"
    r"|(?<!\d)\d{6}(?!\d))"
)


def _addr_has_seed(text: str) -> bool:
    """Стоит ли запускать yargy на сегменте: сильный маркер места ИЛИ широкий
    seed-триггер (а/я, город любого регистра, индекс) ИЛИ house-pattern (безмаркерный
    «топоним+улица-топоним+дом-№», см. ниже). Гейт (_addr_span_strong) отфильтрует лишнее.

    ЭТАП C-fix (agency_0009): безмаркерный склеенный адрес «Москва Ленина 47/3 кв 67»
    в ИЗОЛЯЦИИ сеялся через LOC-хит Natasha, но в КОНТЕКСТЕ документа («3.1. Место
    исполнения обязательств: Москва Ленина …») NER-тэггер LOC на «Москва» уже не даёт
    (сентенс-контекст меняет предсказание), а seed-регэксп маркеров/индекса на этот
    класс не срабатывает — сегмент не отдавался yargy, house-pattern-якорь не получал
    кандидата. Сеем ТЕПЕРЬ и по house-pattern: тот же самый предикат, что и в гейте
    (>=2 Titlecase-топонима + примыкающий короткий номер дома), поэтому расширение
    сеяния НЕ ослабляет точность — что засеяли, то гейт и проверит тем же критерием;
    негативы («Комната переговоров 5», «Договор № 5» — 1 топоним) house-pattern не
    имеют и не сеются/не проходят. Жадность не возвращается."""
    if _ADDR_ANCHOR_RE.search(text) is not None or _ADDR_SEED_RE.search(text) is not None:
        return True
    return _addr_house_pattern(text, _addr_scan_tokens(text, 0, len(text)))


_ADDR_CLUSTER_GAP = 35
# Предел консервативного расширения края в ОДНУ сторону. Типовой хвост адреса
# («, д. 5, кв. 12, стр. 1» / «, помещение 4») короче — предел не даёт расширению
# убежать в прозу, если рядом почему-то не нашлось стоп-слова.
_ADDR_EXPAND_MAX = 48

# Адресные слова для КЛАССИФИКАЦИИ токена при расширении края (не для детекции места).
# Точки в конце снимаются перед проверкой; дефисные формы («р-н», «пр-т») хранятся как есть.
_ADDR_MARKER_WORDS = frozenset(
    "г гор город пос п пгт рп с д дер деревня село мкр мкрн мкр-н обл область край респ "
    "республика р-н район ао округ ст станица х хутор "
    "ул улица пр пр-т пр-кт пркт проспект пер переулок наб набережная ш шоссе б-р бульвар "
    "проезд линия тупик аллея пл площадь квартал тракт "
    "дом корп корпус к стр строение кв квартира оф офис пом помещение лит литера влд "
    "владение зд здание уч участок тер снт днт "
    "км рф россия федерация а/я".split()
)

# Один «токен» при расширении — максимальный кусок без пробелов и запятых.
_ADDR_TOKEN_FWD_RE = re.compile(r"[\s,]*([^\s,]+)")
_ADDR_TOKEN_RE = re.compile(r"[^\s,]+")

# --- Этап C: ЯКОРНАЯ модель адреса (строгий якорь + ограниченный захват) ---------
#
# Адрес НЕ гуляет по падежам (в отличие от ORG/PER) — он встречается 1-2 раза почти
# дословно. Поэтому НЕ реестр+проход2, а СТРОГИЙ ЯКОРЬ: адресный спан-кандидат
# (из LOC/yargy) ПРИНИМАЕТСЯ, только если несёт сильный адресный якорь, и его края
# ОБРЕЗАЮТСЯ по стоп-словам/меткам/границе предложения. Это укрощает жадный yargy:
#   * «3. Споры между Покупателем и Меркурием…» — нет якоря (только «города Москвы» —
#     локальность-одиночка) → НЕ адрес;
#   * «Покупателю»/«Российская Федерация»/«Реквизиты» — нет якоря → НЕ адрес;
#   * «…выдан ОВД … района города Москвы …» — только локальность (район+город), нет
#     улицы/дома/индекса → НЕ адрес (паспортный хвост не тянется, БЕЗ стоп-словаря);
#   * «по адресу: 630099, …, кв. 5» — якорь есть, но «по адресу:» обрезается слева.
#
# КАТЕГОРИИ МАРКЕРОВ. Локальность (город/район/область/страна) САМА ПО СЕБЕ адрес НЕ
# анкорит — иначе одиночный топоним («г. Москва») и паспортный «район города Москвы»
# метились бы адресом. Реальный адрес субъекта несёт УЛИЦУ, ДОМ или ИНДЕКС.
_ADDR_STREET_MK = frozenset(
    "ул улица пер переул переулок пр прт пр-т пр-кт пркт проспект наб набережн набережная "
    "ш шоссе бр б-р бульвар проезд линия туп тупик аллея пл площадь квартал тракт".split()
)
_ADDR_PREMISE_MK = frozenset(
    "дом корп корпус стр строен строение кв квартира оф офис пом помещен помещение "
    "влд владение зд здание лит литера уч участок".split()
)
_ADDR_LOCALITY_MK = frozenset(
    "г гор город пос пгт рп дер деревня село ст станица хутор мкр мкрн мкр-н "
    "обл область кра край респ республика рн р-н район ао округ тер".split()
)
# Однобуквенные/двухбуквенные маркеры распознаём ТОЛЬКО в абревиатурной форме «с точкой»
# («г.», «д.», «ш.», «к.») — иначе предлог «с»/«в»/«к» посреди прозы стал бы «маркером».
_ADDR_SHORT_MK = frozenset("г с п х ш д к".split())
# Токен: НОМЕР-дома (цифры + дробь/дефис + опц. буквенный суффикс: «132/3», «22-й»,
# «6А», «32а») ИЛИ слово (буквы + внутр. дефис, БЕЗ точки). Точка — разделитель:
# «ул.Ленина» без пробела распадается на «ул»+«Ленина» (форма-аббревиатура), а
# принадлежность точки маркеру определяется по СЛЕДУЮЩЕМУ символу (had_dot).
_ADDR_WORD_RE = re.compile(r"\d[\d/\-]*[а-яёa-zА-ЯЁA-Z]{0,3}|[^\W\d_][\w\-]*", re.UNICODE)
_ADDR_INDEX_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_ADDR_HOUSENUM_RE = re.compile(r"^\d{1,4}(?:[/\-][\dа-яёa-z]{1,4})?[а-яёa-z]{0,2}$", re.I)


def _addr_marker_cat(tok: str, next_is_digit: bool, had_dot: bool):
    """Категория адресного маркера токена: 'street'|'premise'|'locality'|None.
    «д.» перед числом — дом (premise), «д.» перед именем — деревня (locality); «к.»
    перед числом — корпус. Короткие маркеры требуют точку СЛЕДОМ (форма «г.»)."""
    core = tok.strip(" \t.,;:()«»\"'").lower().replace(".", "")
    if not core:
        return None
    if core in _ADDR_SHORT_MK and not had_dot:
        return None                      # «с»/«в»/«ш» без точки — не маркер
    if core == "д":
        return "premise" if next_is_digit else "locality"
    if core == "к":
        return "premise" if next_is_digit else None
    if core in _ADDR_STREET_MK:
        return "street"
    if core in _ADDR_PREMISE_MK:
        return "premise"
    if core in _ADDR_LOCALITY_MK:
        return "locality"
    return None


def _addr_scan_tokens(text: str, s: int, e: int):
    """Токенизирует [s,e) для якоря: список (tok_start, tok_end, word, cat, is_num,
    is_cap). cat — категория маркера (или None), is_num — токен-число, is_cap —
    слово-ТОПОНИМ: Titlecase (первая заглавная, НЕ целиком заглавное). ALL-CAPS
    аббревиатуры («ГОСТ», «Р», «ОВД») is_cap НЕ считаются — иначе «ГОСТ Р 52044»
    прошёл бы как «Город Улица Дом»."""
    toks = list(_ADDR_WORD_RE.finditer(text, s, e))
    out = []
    for k, m in enumerate(toks):
        w = m.group(0)
        nxt = toks[k + 1].group(0) if k + 1 < len(toks) else ""
        next_is_digit = bool(nxt) and nxt[0].isdigit()
        had_dot = m.end() < len(text) and text[m.end()] == "."
        cat = _addr_marker_cat(w, next_is_digit, had_dot)
        is_num = w[0].isdigit()
        core = w.strip(" \t.,;:()«»\"'")
        is_cap = (bool(core) and core[0].isupper() and not core.isupper()
                  and cat is None and not is_num)
        out.append((m.start(), m.end(), w, cat, is_num, is_cap))
    return out


_ADDR_HOUSE_GAP = frozenset(" \t.,")


def _addr_house_pattern(text, toks) -> bool:
    """Безмаркерный якорь «улица+дом»: Titlecase-топоним ВПЛОТНУЮ перед коротким
    номером дома («Ленина 5», «Светланская 22», «Мира 68»), И в прогоне >=2 топонима
    (город+улица). «Вплотную» = между ними только пробелы/запятые/точки (не «№»/слова).
    Отсекает «Статья 5» (1 топоним), «ГОСТ Р 52044» (не Titlecase, длинный номер),
    «Приложение № 1 … Договору» («№» между), «Комната переговоров 5» (строчное перед
    числом)."""
    ncap = sum(1 for (_, _, _, _, _, cap) in toks if cap)
    if ncap < 2:
        return False
    for k in range(len(toks) - 1):
        _, e_k, _, _, _, cap = toks[k]
        s_n, e_n, w_n, _, is_num, _ = toks[k + 1]
        gap = text[e_k:s_n]
        # номер-дома НЕ должен быть началом ДАТЫ/десятичной дроби («Москвы 14.05.2015»):
        # если сразу за ним «.цифра» — это дата, не дом (даты — не часть адреса, ТЗ п.2).
        date_tail = re.match(r"\.\d", text[e_n:e_n + 2])
        # За номером дома НЕ должна идти ЕЩЁ группа цифр («Паспорт 43 15 415582» —
        # это реквизит, а не «улица дом»: настоящий номер дома одиночный).
        nn = toks[k + 2] if k + 2 < len(toks) else None
        num_run = nn is not None and nn[4] and all(
            ch in _ADDR_HOUSE_GAP for ch in text[e_n:nn[0]])
        if (cap and is_num and _ADDR_HOUSENUM_RE.match(w_n) and not date_tail
                and not num_run and all(ch in _ADDR_HOUSE_GAP for ch in gap)):
            return True
    return False


# «а/я 15» (абонентский ящик) — премис-эквивалент.
_ADDR_PO_BOX_RE = re.compile(r"(?i)а\s*/\s*я\s*\d")


def _addr_valid_index(text, toks) -> bool:
    """Почтовый индекс как якорь — ТОЛЬКО когда 6-значная группа ВПЛОТНУЮ (через
    пробелы/запятые) прилегает к городу/локальности/топониму: «420111, г. Казань»,
    «Одинцово, 143000». Голая 6-значная группа паспорта/кода подразделения к городу
    не прилегает («…подразделения 770091», «412957, выдан …») — не якорь. Так индекс
    возвращает адреса «город+индекс» без улицы, НЕ воскрешая паспортные FP."""
    for k, (s_k, e_k, w_k, cat_k, is_num_k, cap_k) in enumerate(toks):
        if not (is_num_k and _ADDR_INDEX_RE.fullmatch(w_k)):
            continue
        # сосед слева/справа — локальность-маркер ИЛИ Titlecase-топоним, вплотную
        for j in (k - 1, k + 1):
            if not (0 <= j < len(toks)):
                continue
            s_j, e_j, w_j, cat_j, is_num_j, cap_j = toks[j]
            lo, hi = (e_j, s_k) if j < k else (e_k, s_j)
            gap = text[lo:hi]
            if (cat_j in ("locality", "street", "premise") or cap_j) and \
                    all(ch in _ADDR_HOUSE_GAP for ch in gap):
                return True
    return False


def _addr_span_strong(text: str, s: int, e: int) -> bool:
    """Сильный адресный якорь на спане [s,e). Локальность-одиночка и ГОЛЫЙ ИНДЕКС
    (не прилегающий к городу) НЕ якорь: 6-значная группа паспорта/кода подразделения —
    ложный «индекс». Якорь =
      * УЛИЦА с топонимом/номером рядом («ул. Ленина», «ш. Тверская»); ИЛИ
      * ПОМЕЩЕНИЕ с номером («д. 18», «кв. 5», «а/я 15»); ИЛИ
      * БЕЗМАРКЕРНЫЙ «улица+дом» (Titlecase-топоним + номер, >=2 топонима); ИЛИ
      * ИНДЕКС, ПРИЛЕГАЮЩИЙ к городу/топониму («420111, г. Казань»)."""
    toks = _addr_scan_tokens(text, s, e)
    cats = {c for (_, _, _, c, _, _) in toks if c}
    has_num = any(n for (_, _, _, _, n, _) in toks)
    has_cap = any(c for (_, _, _, _, _, c) in toks)
    if "street" in cats and (has_cap or has_num):
        return True
    if "premise" in cats and has_num:
        return True
    if _ADDR_PO_BOX_RE.search(text[s:e]):
        return True
    if _addr_house_pattern(text, toks):
        return True
    if _addr_valid_index(text, toks):
        return True
    return False


# Граница предложения внутри адресного кандидата: «Федерации. Стороны» — точка после
# слова-НЕ-аббревиатуры, за которой (через пробелы) заглавная. «д. 18»/«стр. 1» —
# точка после адресной аббревиатуры, НЕ граница. Перенос строки — всегда граница.
_ADDR_ABBR_BEFORE_DOT = (
    _ADDR_STREET_MK | _ADDR_PREMISE_MK | _ADDR_LOCALITY_MK | _ADDR_SHORT_MK
)


def _addr_split_sentences(text: str, s: int, e: int):
    """Дробит [s,e) по границам предложений (перенос строки; точка/!/? после
    слова-не-аббревиатуры перед заглавной). Возвращает список подспанов."""
    parts = []
    cut = s
    i = s
    while i < e:
        ch = text[i]
        boundary = False
        if ch in "\n\r":
            boundary = True
            end_here = i
        elif ch in ".!?":
            # токен непосредственно слева (слово ИЛИ число)
            j = i - 1
            while j >= s and (text[j].isalnum() or text[j] == "-"):
                j -= 1
            word = text[j + 1:i].lower().replace(".", "")
            k = i + 1
            while k < e and text[k] in " \t":
                k += 1
            nxt_upper = k < e and text[k].isupper()
            # граница, если слева НЕ адресная аббревиатура (обычное слово ИЛИ число —
            # «…кв. 5. Покупателю»: точка после номера дома завершает адрес), а справа
            # заглавная. Одиночная БУКВА слева — инициал/аббревиатура («В.О.», «А.С.»),
            # не конец предложения. Пустой токен (двойная пунктуация) — не граница.
            is_abbr = (
                (word in _ADDR_ABBR_BEFORE_DOT and not word.isdigit())
                or (len(word) == 1 and word.isalpha())
            )
            if word and not is_abbr and nxt_upper:
                boundary = True
                end_here = i + 1
        if boundary:
            if end_here > cut:
                parts.append((cut, end_here))
            cut = i + 1
        i += 1
    if e > cut:
        parts.append((cut, e))
    return parts


# Лениентный набор маркерных слов для ОБРЕЗКИ КРАЁВ (без требования точки к коротким):
# ведущее «г»/«д»/«ул» — часть адреса, не обрезаем; ведущее «по»/«адресу»/«место» — метка.
_ADDR_MARKER_ANY = (
    _ADDR_STREET_MK | _ADDR_PREMISE_MK | _ADDR_LOCALITY_MK | _ADDR_SHORT_MK
)


def _addr_is_marker_word(tok: str) -> bool:
    core = tok.strip(" \t.,;:()«»\"'").lower().replace(".", "")
    return core in _ADDR_MARKER_ANY


def _addr_strip_edges(text: str, s: int, e: int) -> tuple[int, int]:
    """Обрезает ТОЛЬКО ведущие метки/предлоги («по адресу:», «Реквизиты») и краевую
    пунктуацию. Внутренность и правый край НЕ трогаем: _build_address_spans уже
    останавливает расширение на стоп-словах, а над-закрытие метки золотой контракт
    допускает. Ведущий строчный токен снимаем, лишь если он НЕ маркер, НЕ топоним
    (Titlecase), НЕ число — то есть предлог/метка/глагол."""
    toks = _addr_scan_tokens(text, s, e)
    i = 0
    while i < len(toks):
        _, _, w, cat, is_num, is_cap = toks[i]
        if cat or is_num or is_cap or _addr_is_marker_word(w):
            break
        i += 1
    if i >= len(toks):
        return s, s
    ns = toks[i][0]
    ne = e
    while ne > ns and text[ne - 1] in " \t.,;:":
        ne -= 1
    return ns, ne


def _finalize_address_spans(text, raw_spans, occupied):
    """Этап C: из сырых адресных спанов (LOC∪yargy, кластеризованных/расширенных)
    оставляет только НАСТОЯЩИЕ адреса. Каждый спан: режем по границам предложений →
    обрезаем края до якорного содержимого → ПОДРЕЗАЕМ к барьерам ORG/PER/реквизитов
    → пропускаем ТОЛЬКО при сильном якоре. Барьеры подрезаются ДО проверки якоря:
    иначе «Паспорт 43 15 415582, выдан…» проходит через ложный house-pattern
    «Паспорт 43», а подрезка паспорта оставляет ложный адресный хвост «, выдан …»."""
    out = []
    for rs, re_ in raw_spans:
        for ss, se in _addr_split_sentences(text, rs, re_):
            ts, te = _addr_strip_edges(text, ss, se)
            if ts >= te:
                continue
            if occupied:
                ts, te = _clip_edges(ts, te, occupied)
                ts, te = _addr_strip_edges(text, ts, te)   # снять край, оголённый подрезкой
            if ts < te and _addr_span_strong(text, ts, te):
                out.append((ts, te))
    return out


def _overlaps_any(s: int, e: int, spans) -> bool:
    """True, если [s, e) пересекается хотя бы с одним интервалом из spans."""
    return any(max(s, os) < min(e, oe) for os, oe in spans)


def _addr_transparent(text: str, s: int, e: int) -> bool:
    """True, если интервал [s, e) — это АДРЕСНЫЙ ХВОСТ, ошибочно помеченный NER как
    PER/ORG (напр. «Советская 12», «Свободы 46»: улица + номер дома NER часто тэггит
    ORG). Такой спан НЕ должен быть барьером для адреса — адрес обязан его закрыть.
    Отличаем от НАСТОЯЩЕГО соседа (ФИО «Иванов Иван Иванович», «ООО «Ромашка»») по
    номеру дома: адресный хвост несёт цифру-номер И состоит целиком из адресно-
    классифицируемых токенов; ФИО/ORG-название номера дома не имеют. regex-реквизиты
    сюда НЕ попадают (проверяются отдельно и всегда остаются барьером — иначе утечка)."""
    toks = _ADDR_TOKEN_RE.findall(text[s:e])
    if not toks:
        return False
    if not any(any(ch.isdigit() for ch in tk) for tk in toks):
        return False                              # нет номера дома — это не адресный хвост
    return all(_classify_addr_token(tk) == "addr" for tk in toks)


def _address_barriers(
    text: str,
    ner_spans: list[tuple[int, int]],
    regex_spans: list[tuple[int, int]],
    org_spans: list[tuple[int, int]] = (),
) -> list[tuple[int, int]]:
    """Занятая территория, на которую расширение адреса НЕ наступает: все regex-реквизиты
    (всегда) + все структурные ORG-спаны движка anchor_registry (всегда, этап A' —
    это уже подтверждённый якорем/реестром реальный ORG, не сырая догадка NER, поэтому
    _addr_transparent к ним не применяется) + те Natasha PER/ORG, что НЕ являются
    адресным хвостом (_addr_transparent). Общая для основного прохода и граничного
    (B3) — геометрия барьеров одна."""
    barriers = list(regex_spans) + list(org_spans)
    barriers += [(s, e) for s, e in ner_spans if not _addr_transparent(text, s, e)]
    return barriers


def _clip_edges(s: int, e: int, occupied) -> tuple[int, int]:
    """Обрезает КРАЯ адресного спана до границы чужой сущности. Нужно потому, что сам
    yargy иногда включает соседнее ФИО/ORG в адресный парс («д. 8 Иванов»): расширение
    тут ни при чём, спан «грязный» с самого начала. Режем только край, упирающийся в
    occupied (внутренние пересечения — редки и добираются обрезкой в _resolve_overlaps);
    середину адреса не трогаем. Адрес закрывается вплотную ДО чужой сущности — это НЕ
    сужение: та территория покрыта своим токеном, ничего не утекает."""
    changed = True
    while changed and s < e:
        changed = False
        for os_, oe in occupied:
            if os_ > s and os_ < e <= oe:        # occ накрывает правый край, слева есть адрес
                e = os_
                changed = True
            elif oe < e and os_ <= s < oe:       # occ накрывает левый край, справа есть адрес
                s = oe
                changed = True
    return s, e


def _classify_addr_token(tok: str) -> str:
    """Классифицирует токен на границе адреса: 'addr' (часть адреса — продолжать
    расширение), 'skip' (пустой/пунктуация — пройти, но не расширять), 'stop'
    (незнакомое слово в нижнем регистре — мы вышли из адреса)."""
    core = tok.strip(" \t.,;:()«»\"'")
    if not core:
        return "skip"
    if any(ch.isdigit() for ch in core):
        return "addr"                         # индекс/дом/«22-й»/«132/3»/«6А»
    low = core.lower().rstrip(".")
    if low in _ADDR_MARKER_WORDS:
        return "addr"                         # адресный маркер (тип улицы/дома/поселения)
    if core[0].isupper():
        return "addr"                         # топоним / название улицы (имя собственное)
    return "stop"                             # слово нижнего регистра, не маркер — конец адреса


def _expand_addr_right(text: str, end: int, occupied=()) -> int:
    """Тянет правый край адреса по адресным токенам, НИКОГДА не сужая.
    Останавливается на первом не-адресном слове, на пределе _ADDR_EXPAND_MAX
    или ВПЛОТНУЮ ДО начала чужой сущности (occupied): расширение не наступает на
    территорию, уже занятую regex/PER/ORG-токеном — там ничего не утекает, зона
    покрыта другим токеном. Асимметрия «при сомнении шире» действует только на
    свободном тексте между концом адреса и началом чужой сущности."""
    p = end
    limit = min(len(text), end + _ADDR_EXPAND_MAX)
    while p < len(text):
        m = _ADDR_TOKEN_FWD_RE.match(text, p)
        if m is None or m.start(1) >= limit:
            break
        if _overlaps_any(m.start(1), m.end(1), occupied):
            break                                 # уткнулись в чужую сущность — стоп
        cls = _classify_addr_token(m.group(1))
        if cls == "stop":
            break
        p = m.end(1)
        if cls == "addr":
            end = p
    return end


# Левое расширение УЖЕ правого: тянем влево только ведущий индекс/регион/страну
# (то, что грамматика yargy роняет слева: «Российская Федерация, 101000, …»). НЕ
# грабим произвольное слово с заглавной буквы — иначе съедаем метку ячейки («Адрес:»,
# «Юридический адрес:») в токен, теряя контекст для LLM. Асимметрия слева слабее:
# страна/регион — наименее идентифицирующая часть, её недозакрытие не утечка дома/кв.
_ADDR_LEFT_WORDS = frozenset(
    "россия российская российской российскую федерация федерации рф".split()
)


def _classify_addr_left_token(tok: str) -> str:
    """Классификатор ЛЕВОГО расширения (уже правого: левый край не должен съедать
    метку ячейки «Адрес:» в токен). Три исхода:
      'commit' — цифра (индекс) / адресный маркер / форма страны: фиксируем сюда;
      'pass'   — пустой ИЛИ слово с ЗАГЛАВНОЙ (метка/регион-прилагательное): проходим
                 сквозь, но не фиксируем — впитается, лишь если левее есть 'commit';
      'stop'   — слово нижнего регистра не-маркер: вышли из адреса влево."""
    core = tok.strip(" \t.,;:()«»\"'")
    if not core:
        return "pass"
    if any(ch.isdigit() for ch in core):
        return "commit"
    low = core.lower().rstrip(".")
    if low in _ADDR_MARKER_WORDS or low in _ADDR_LEFT_WORDS:
        return "commit"
    if core[0].isupper():
        return "pass"
    return "stop"


def _expand_addr_left(text: str, start: int, occupied=()) -> int:
    """Тянет левый край адреса влево по индексу/региону/маркерам, НИКОГДА не сужая.
    Заглавное слово впитывается, лишь если левее нашёлся фиксируемый адресный токен —
    так «обл. Московская,»/«Российская Федерация,» входят, а метка «Адрес:» нет.
    Останавливается ВПЛОТНУЮ ПОСЛЕ чужой сущности (occupied): расширение влево не
    заходит на территорию regex/PER/ORG-токена (симметрично правому расширению)."""
    bound = max(0, start - _ADDR_EXPAND_MAX)
    prefix = text[bound:start]
    new_start = start
    for m in reversed(list(_ADDR_TOKEN_RE.finditer(prefix))):
        abs_s, abs_e = bound + m.start(), bound + m.end()
        if _overlaps_any(abs_s, abs_e, occupied):
            break                                 # уткнулись в чужую сущность — стоп
        cls = _classify_addr_left_token(m.group(0))
        if cls == "stop":
            break
        if cls == "commit":
            new_start = abs_s
        # 'pass': идём влево, не фиксируя (впитается ретроспективно при commit)
    return new_start


def _filter_suspect_yargy(
    text: str,
    yargy_spans: list[tuple[int, int]],
    ner_spans: list[tuple[int, int]],
    loc_spans: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Отбрасывает yargy-спаны, похожие на ложное срабатывание на ФИО/названии: они
    ПЕРЕКРЫВАЮТ уже найденную PER/ORG-сущность и при этом НЕ подкреплены ни LOC, ни
    адресным маркером внутри себя. yargy иногда принимает последовательность заглавных
    (имя) за топоним; такой обрывок, склеенный кластеризацией с настоящим адресом рядом,
    затянул бы ФИО и метку («адрес:») в ADDRESS-токен (человек мислейбелится адресом,
    своего PERSON-токена не получает). LOC/маркеры/расширение восстанавливают настоящий
    адрес и без этого спана, поэтому его снятие безопасно для полноты (проверено GOLDEN)."""
    if not ner_spans:
        return yargy_spans
    kept: list[tuple[int, int]] = []
    for s, e in yargy_spans:
        overlaps_ner = any(max(s, ns) < min(e, ne) for ns, ne in ner_spans)
        overlaps_loc = any(max(s, ls) < min(e, le) for ls, le in loc_spans)
        has_marker = _ADDR_ANCHOR_RE.search(text[s:e]) is not None
        if overlaps_ner and not overlaps_loc and not has_marker:
            continue
        kept.append((s, e))
    return kept


def _build_address_spans(
    text: str,
    raw_spans: list[tuple[int, int]],
    occupied: list[tuple[int, int]] = (),
) -> list[tuple[int, int]]:
    """Собирает финальные адресные спаны из «сырых» хитов (LOC ∪ yargy):
    кластеризует по близости, расширяет края по адресным токенам, повторно
    склеивает пересечения, возникшие от расширения. Полнота спана важнее точности
    границ (см. асимметрию цены).

    occupied — интервалы уже найденных ЧУЖИХ сущностей (regex-реквизиты, PER/ORG):
    расширение края останавливается вплотную до них, не поглощая соседнее поле
    (иначе следующая сущность теряет свой токен, а при regex-хвосте — утечка ПДн)."""
    spans = sorted({(s, e) for s, e in raw_spans if 0 <= s < e})
    if not spans:
        return []

    clusters: list[tuple[int, int]] = []
    cs, ce = spans[0]
    for s, e in spans[1:]:
        if s - ce <= _ADDR_CLUSTER_GAP:
            ce = max(ce, e)
        else:
            clusters.append((cs, ce))
            cs, ce = s, e
    clusters.append((cs, ce))

    expanded = [
        (_expand_addr_left(text, s, occupied), _expand_addr_right(text, e, occupied))
        for s, e in clusters
    ]

    expanded.sort()
    merged = [expanded[0]]
    for s, e in expanded[1:]:
        ps, pe = merged[-1]
        if s <= pe:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))

    # Обрезаем края спанов до границ чужих сущностей (yargy мог захватить соседнее поле).
    if occupied:
        clipped = []
        for s, e in merged:
            s, e = _clip_edges(s, e, occupied)
            if s < e:
                clipped.append((s, e))
        return clipped
    return merged


def _load_ner_config(config_path: str) -> tuple[dict, list[str]]:
    """Читает entity_types.yaml и возвращает:
      - ner_label_map: {метка Natasha -> entity_type} для записей method: ner с ner_label
        (напр. {"PER": "PERSON", "ORG": "ORG"});
      - addr_types: список entity_type для записей method: ner с ner_extractor: addr
        (обычно ["ADDRESS"]).
    Записи с enabled: false пропускаются. Файл обязан существовать — иначе FileNotFoundError.
    """
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    ner_label_map: dict[str, str] = {}
    addr_types: list[str] = []

    for entity_type, spec in config["entity_types"].items():
        if not isinstance(spec, dict) or spec.get("method") != "ner":
            continue
        if spec.get("enabled", True) is False:
            continue

        ner_label = spec.get("ner_label")
        ner_extractor = spec.get("ner_extractor")

        if ner_extractor == "addr":
            addr_types.append(entity_type)
        elif ner_label is not None:
            ner_label_map[ner_label] = entity_type
        else:
            print(
                f"ПРЕДУПРЕЖДЕНИЕ: тип {entity_type} (method: ner) без ner_label и без "
                f"ner_extractor: addr — пропущен",
                file=sys.stderr,
            )

    return ner_label_map, addr_types


def _expand_person_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Расширяет спан PERSON инициалами "И.И." в обе стороны независимо.
    Возвращает новые (start, end)."""
    right = _INITIALS_RIGHT.match(text[end:])
    if right is not None:
        end = end + right.end()

    left = _INITIALS_LEFT.search(text[:start])
    if left is not None:
        start = left.start()

    return start, end


def _glue_address_matches(text: str, matches: list) -> list[tuple[int, int]]:
    """Склеивает соседние Match'и AddrExtractor в цепочки-адреса.
    Соседние склеиваются, если разрыв между match_i.stop и match_{i+1}.start
    не длиннее 6 символов и целиком состоит из [ ,.\\u00A0] (без букв).
    Возвращает список (start, end) итоговых спанов."""
    if not matches:
        return []

    ordered = sorted(matches, key=lambda m: (m.start, m.stop))

    spans: list[tuple[int, int]] = []
    cur_start = ordered[0].start
    cur_end = ordered[0].stop

    for m in ordered[1:]:
        gap = text[cur_end:m.start]
        glue = (
            0 <= len(gap) <= _ADDR_GAP_MAXLEN
            and all(ch in _ADDR_GAP_CHARS for ch in gap)
        )
        if glue:
            cur_end = m.stop
        else:
            spans.append((cur_start, cur_end))
            cur_start = m.start
            cur_end = m.stop

    spans.append((cur_start, cur_end))
    return spans


def detect_ner(
    doc: SourceDocument,
    config_path: str,
    regex_entities: list[Entity] | None = None,
    org_entities: list[Entity] | None = None,
    per_entities: list[Entity] | None = None,
) -> list[Entity]:
    """regex_entities — результат detect_regex, ЕСЛИ он уже посчитан вызывающим
    (нормативный путь: реквизиты дают «карту занятой территории» ДО расширения
    адреса, см. shifrator.py). Без него расширение всё равно уступает своим PER/ORG,
    а пересечение с regex-хвостом ловится обрезкой в _resolve_overlaps (страховка).

    org_entities — результат anchor_registry.detect_org, ЕСЛИ он уже посчитан
    вызывающим (этап A': ORG теперь считается ДО detect_ner, см. shifrator.py). Это
    структурный барьер расширения адреса — закрывает A-4 (адрес поглощал обломок
    готового ORG, lease_0008). Без него расширение адреса просто не видит ORG вовсе
    (Natasha-ORG в конфиге выключена, см. этап A).

    per_entities — структурные PER-сущности anchor_registry (этап B): такой же
    безусловный барьер расширения адреса, что ORG. Natasha-PER в конфиге выключена
    (structure-first PER), поэтому адрес больше не видит людей через NER — барьер
    ставит готовый PER-спан, чтобы адрес не заходил на ФИО (подготовка к этапу C)."""
    ner_label_map, addr_types = _load_ner_config(config_path)

    # Карта regex-территории по сегментам: расширение адреса на неё не наступает.
    regex_by_seg: dict[str, list[tuple[int, int]]] = {}
    if regex_entities:
        for e in regex_entities:
            regex_by_seg.setdefault(e.segment_id, []).append((e.start, e.end))

    # Карта структурных ORG-спанов (anchor_registry) по сегментам — тот же
    # безусловный барьер, что regex, см. docstring _address_barriers.
    org_by_seg: dict[str, list[tuple[int, int]]] = {}
    if org_entities:
        for e in org_entities:
            org_by_seg.setdefault(e.segment_id, []).append((e.start, e.end))

    # Структурные PER-спаны (этап B) — тот же безусловный барьер, что ORG.
    per_by_seg: dict[str, list[tuple[int, int]]] = {}
    if per_entities:
        for e in per_entities:
            per_by_seg.setdefault(e.segment_id, []).append((e.start, e.end))

    entities: list[Entity] = []

    for segment in doc.segments:
        orig_text = segment.text
        if not orig_text:
            continue

        # Весь путь детекции (Doc, расширение инициалов, AddrExtractor, склейка окон)
        # работает с НОРМАЛИЗОВАННОЙ копией (этап 2): омоглифы сведены, невидимые
        # сняты, разделители внутри числа схлопнуты, регистр нормализован (через
        # detection_text как базу). Все внутренние спаны (loc/ner/yargy/барьеры) —
        # в координатах ЭТОЙ копии. original_text у каждого Entity вырезается из
        # НАСТОЯЩЕГО segment.text (orig_text), а спан [start,end) отображается из
        # норм-координат в исходные через offset_map при СОЗДАНИИ Entity. На
        # неискажённом тексте нормализация — тождество, поведение прежнее.
        text, offset_map = detection_view(segment)

        # --- NER-тэггер Natasha: PERSON/ORG (по конфигу) + LOC-спаны (для адреса) ---
        # Тэггер нужен и для адреса (LOC — контекстный детектор места), поэтому
        # запускаем его, если сконфигурирован хоть PER/ORG, хоть ADDRESS.
        loc_spans: list[tuple[int, int]] = []
        ner_spans: list[tuple[int, int]] = []   # PER/ORG-спаны для отсева yargy-ФИО-ложняков
        if ner_label_map or addr_types:
            nlp_doc = Doc(text)
            nlp_doc.segment(_segmenter)
            nlp_doc.tag_ner(_ner_tagger)
            for span in nlp_doc.spans:
                if span.type == "LOC":
                    loc_spans.append((span.start, span.stop))
                    continue
                entity_type = ner_label_map.get(span.type)
                if entity_type is None:
                    continue  # метка Natasha не сопоставлена ни одному типу конфига

                start, end = span.start, span.stop
                if span.type == "PER":
                    start, end = _expand_person_span(text, start, end)
                ner_spans.append((start, end))   # норм-координаты (для барьеров)

                src_start, src_end = norm_to_src(offset_map, start, end)
                entities.append(Entity(
                    id=str(uuid.uuid4()),
                    segment_id=segment.id,
                    start=src_start,
                    end=src_end,
                    original_text=orig_text[src_start:src_end],
                    entity_type=entity_type,
                    detector="ner",
                    confidence=1.0,
                ))

        # --- ADDRESS: гибрид «LOC/маркеры детектируют место → yargy-парсер добирает
        # полный спан → консервативное расширение закрывает хвост». yargy НЕ гоняется
        # по сегментам без хита (главный источник ускорения этапа A). ---
        if addr_types:
            if loc_spans or _addr_has_seed(text):
                # yargy — только здесь (окрестность хита), не по всему тексту
                yargy_spans = _glue_address_matches(text, list(_addr_extractor(text)))
                # Структурные ORG/PER-спаны (anchor_registry, этапы A/B) в координатах
                # норм-текста — нужны и для ОТСЕВА yargy-ложняков (ниже), и как барьеры
                # расширения. regex/ORG/PER хранятся в ИСХОДНЫХ координатах.
                regex_src = regex_by_seg.get(segment.id, [])
                regex_norm = [src_to_norm(offset_map, s, e) for s, e in regex_src]
                org_src = org_by_seg.get(segment.id, [])
                org_norm = [src_to_norm(offset_map, s, e) for s, e in org_src]
                per_src = per_by_seg.get(segment.id, [])
                per_norm = [src_to_norm(offset_map, s, e) for s, e in per_src]
                # Отсев yargy-ложняков на ФИО/названиях (перекрывают PER/ORG без LOC/
                # маркера): иначе кластеризация затянула бы имя и метку в ADDRESS-токен.
                # ЭТАП B: Natasha-PER/ORG выключены, поэтому ner_spans почти пуст —
                # источник «занятых» ФИО/ORG-спанов теперь СТРУКТУРНЫЙ движок (per/org_norm).
                # Без них yargy-ложняк на «Иванов» сшивается с реальным адресом и тянет
                # метку «адрес:» в токен (регресс, вскрытый отключением Natasha-PER).
                name_spans = list(ner_spans) + list(org_norm) + list(per_norm)
                yargy_spans = _filter_suspect_yargy(text, yargy_spans, name_spans, loc_spans)
                # Сам адрес подтверждают LOC и/или yargy (маркер — лишь триггер запуска):
                # маркерные позиции в «сырьё» НЕ кладём, иначе список маркеров стал бы
                # самостоятельным детектором. Расширение краёв использует маркеры как
                # линейку, но стартует только от подтверждённых LOC/yargy-спанов.
                occupied = _address_barriers(
                    text, ner_spans, regex_norm, list(org_norm) + list(per_norm)
                )
                raw_addr = _build_address_spans(
                    text, loc_spans + yargy_spans, occupied
                )
                # ЭТАП C: строгий якорь + обрезка краёв поверх сырых спанов yargy/LOC.
                for start, end in _finalize_address_spans(text, raw_addr, occupied):
                    src_start, src_end = norm_to_src(offset_map, start, end)
                    for addr_type in addr_types:
                        entities.append(Entity(
                            id=str(uuid.uuid4()),
                            segment_id=segment.id,
                            start=src_start,
                            end=src_end,
                            original_text=orig_text[src_start:src_end],
                            entity_type=addr_type,
                            detector="ner",
                            confidence=1.0,
                        ))

    # Инвариант блока 3: original_text строго равен срезу сегмента по [start:end].
    for e in entities:
        seg = next(s for s in doc.segments if s.id == e.segment_id)
        actual = seg.text[e.start:e.end]
        if e.original_text != actual:
            raise AssertionError(
                f"Нарушен инвариант original_text для {e.entity_type} в {e.segment_id} "
                f"[{e.start}:{e.end}]: ожидалось {e.original_text!r}, получено {actual!r}"
            )

    return entities
