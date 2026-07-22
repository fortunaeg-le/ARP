"""Этап A (structure-first ORG) — двухпроходный СТРУКТУРНЫЙ детектор организаций.

ПРОБЛЕМА (REAL-GENRE, см. archive/reports/HANDOFF_A0_GLINER.md). NER-тэггер Natasha
на юридической прозе метит ORG-мусор (Большинство→ORG, Прибыль→ADDRESS, ПОДПИСИ
СТОРОН→ORG), а одно и то же имя фирмы в разных падежах получает РАЗНЫЕ типы
(«Восход» → PER/ADDR/ORG вперемешку). Bake-off A0 решил: строить структурный NER,
Natasha-ORG на прозе выключить.

ИДЕЯ. Организация вводится в документ ОДИН раз «по правилам жанра» — это ЯКОРЬ
(org-форма рядом, имя в кавычках, конструкция введения «далее — …», соседство с
валидированным ИНН/ОГРН юрлица). Дальше имя гуляет по падежам — ловим НЕ повторным
NER, а СОПОСТАВЛЕНИЕМ по лемме (pymorphy, тот же, что под Natasha) с ЯКОРЕМ,
зафиксированным в РЕЕСТРЕ документа.

АРХИТЕКТУРА (типо-параметрическая, чтобы след. этапы подключили PER/ADDRESS СВОИМИ
детекторами якорей, не переписывая движок):

  1. AnchorDetector — pluggable-интерфейс детектора якорей одного типа. Отдаёт список
     Anchor: ключ (лемма отличительного ЯДРА), полный спан упоминания, доказательства,
     уверенность. Здесь реализован ОДИН: OrgAnchorDetector.
  2. Registry — реестр документа: ключ (лемма ядра) → тип → каноническая форма →
     доказательства → уверенность. Арбитраж конфликтов ТИПОВ — при записи (в этом
     прототипе тип один, но seam для B заложен).
  3. Проход 2 — вхождение, чья лемма совпадает с ключом реестра, метится ORG ПОСЛЕ
     guard (коллизия леммы: ООО «Заря» vs «на заре»). Natasha-ORG не спрашивается нигде.
  4. Guard — процедура точности на кандидате прохода 2 (кавычки / org-форма рядом /
     регистр × позиция в предложении). Точность важнее полноты: сомнение = не метить.

Восстановление — плейсхолдеры 1:1, точная поверхностная форма исходного спана; канон
из реестра в текст НЕ попадает (каждый Entity несёт свой original_text — инвариант B4).

Реестр живёт per-документ в рантайме, между документами не сохраняется.
"""

import re
import uuid
from dataclasses import dataclass, field

from models import Entity, SourceDocument
from normalizer import norm_to_src, normalize_for_detection, src_to_norm

# pymorphy-анализатор берём тот же, что уже загружен под Natasha (см. ТЗ: «pymorphy
# уже есть под Наташей»). Это НЕ обращение к NER Natasha — только морфология лемм.
from ner_detector import _morph_vocab as _MORPH


# --------------------------------------------------------------------------- #
#         Поисковое представление ЯКОРЯ (этап A′-1, ЛОКАЛЬНО в детекторе)     #
# --------------------------------------------------------------------------- #
# Мутации-«разрывы» (перенос строки внутри слова, невидимые/NBSP-разделители,
# внедрённые ВНУТРЬ «ПАО»/«ООО»/имени в кавычках) рвут регэкспы якоря на уровне
# ТОКЕНА: \W-граница \n обрывает _WORD_RE точно так же, как настоящий пробел, а
# NBSP/NNBSP к моменту стандартного detection_view (Stage 2, normalizer.py) уже
# необратимо стали ОБЫЧНЫМ пробелом — отличить «пробел, который на самом деле
# NBSP внутри слова» от настоящего пробела на этом этапе уже нельзя. Поэтому для
# ПОИСКА якоря строим СВОЙ (независимый от Stage 2) вид: сперва вырезаем разрывающие
# символы из detection_text/segment.text НАПРОЧЬ (до того, как Stage 2 успеет
# превратить NBSP в пробел), потом прогоняем через тот же normalize_for_detection
# (омоглифы/регистр — как обычно). Итоговая карта индексов ведёт СРАЗУ в
# segment.text, минуя Stage-2 norm — но семантика та же: только ПОИСК меняется,
# спан для маски/восстановления идёт через эту же карту в исходные координаты
# (norm_to_src ниже), поэтому в маску попадает ИСХОДНЫЙ байтовый диапазон целиком,
# включая все вырезанные при поиске символы.
_BREAKING_CHARS = frozenset(
    "\n\r"       # перенос строки/CR — рвёт слово при переносе-мутации
    "\xad"       # SOFT HYPHEN
    "\u200b"     # ZERO WIDTH SPACE
    "\u2060"     # WORD JOINER
    "\u200c"     # ZERO WIDTH NON-JOINER
    "\u200d"     # ZERO WIDTH JOINER
    "\u202f"     # NARROW NO-BREAK SPACE
    "\xa0"       # NO-BREAK SPACE
)


def _strip_breaking(text: str, nl_to_space: bool = False) -> tuple[str, list[int]]:
    """Убирает _BREAKING_CHARS из text (только вырезание, без замены пробелом —
    иначе «ПА\\nО» не схлопнется обратно в «ПАО»). Возвращает (out, idx), idx[k] —
    позиция out[k] в text (строго возрастающая, как offset_map по всему пайплайну).

    nl_to_space=True (для PER): перенос строки/CR НЕ вырезаются, а заменяются пробелом.
    Причина — ключ ORG обычно ОДНОТОКЕННЫЙ («Ромашка»/«Восход»), и `\\n` в него попадает
    ВНУТРИ слова (мутация t_linebreak ставит его в случайную позицию), поэтому вырезание
    склеивает форму обратно. Ключ PER — МНОГОТОКЕННЫЙ («Беляев Олег Олегович»), и `\\n`
    ложится на ГРАНИЦУ слов; вырезание склеило бы «БеляевОлег» в один нераспознаваемый
    токен. Прочие разрывы (NBSP/ZWSP/…) мутация t_invisible вставляет ВНУТРИ слова, не
    трогая исходный пробел, поэтому их вырезаем в обоих режимах."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text):
        if ch in _BREAKING_CHARS:
            if nl_to_space and ch in "\n\r":
                out.append(" ")
                idx.append(i)
            continue
        out.append(ch)
        idx.append(i)
    return "".join(out), idx


def _compose(outer_to_mid: list[int], mid_to_inner: list[int]) -> list[int]:
    """Композиция двух карт индексов: outer_idx -> inner_idx."""
    return [mid_to_inner[i] for i in outer_to_mid]


def anchor_search_view(segment) -> tuple[str, list[int]]:
    """(search_text, offset_map) для ПОИСКА ЯКОРЯ. offset_map индексирует
    segment.text напрямую (как и все карты пайплайна) — композиция «вырезать
    разрывающие» + «normalize_for_detection» на очищенном тексте. Кэшируется в
    segment.metadata под отдельным ключом (НЕ пересекается с _norm_cache
    detection_view — это независимый вид, не общий пайплайн Stage 2)."""
    md = segment.metadata
    cached = md.get("_anchor_search_cache")
    if cached is not None:
        return cached
    base = md.get("detection_text", segment.text)
    stripped, stripped_to_base = _strip_breaking(base)
    search, search_to_stripped = normalize_for_detection(stripped)
    search_to_base = _compose(search_to_stripped, stripped_to_base)
    result = (search, search_to_base)
    md["_anchor_search_cache"] = result
    return result


def per_search_view(segment) -> tuple[str, list[int]]:
    """Поисковый вид для PER: как anchor_search_view, но перенос строки/CR внутри
    сущности заменяется ПРОБЕЛОМ, а не вырезается (см. _strip_breaking про
    много-/однотокенные ключи). Отдельный кэш-ключ — вид независимый."""
    md = segment.metadata
    cached = md.get("_per_search_cache")
    if cached is not None:
        return cached
    base = md.get("detection_text", segment.text)
    stripped, stripped_to_base = _strip_breaking(base, nl_to_space=True)
    search, search_to_stripped = normalize_for_detection(stripped)
    search_to_base = _compose(search_to_stripped, stripped_to_base)
    result = (search, search_to_base)
    md["_per_search_cache"] = result
    return result


# --------------------------------------------------------------------------- #
#                     Нормализация кавычек (ЛОКАЛЬНО в детекторе)              #
# --------------------------------------------------------------------------- #
# В общий пайплайн нормализация кавычек не входит, поэтому она ЗДЕСЬ (требование
# ТЗ). Замена строго 1:1 по символам (длина сохраняется) — поэтому спаны,
# найденные в норм-тексте, остаются валидными координатами anchor_search_view.
_OPEN_QUOTES = "«“„‹"
_CLOSE_QUOTES = "»”›"
_ASCII_QUOTE = '"'          # ASCII-кавычка неоднозначна (и откр., и закр.)

# Ядро в кавычках: гильеметы, типографские, ASCII. Ограничиваем длину ядра, чтобы
# не проглотить абзац между случайной парой кавычек.
_QUOTED_RE = re.compile(
    r"«([^«»\n]{1,60})»"
    r"|“([^“”\n]{1,60})”"
    r"|„([^“”\"\n]{1,60})[“”\"]"
    r"|\"([^\"\n]{1,60})\""
)


def _find_quoted(norm: str):
    """Список кортежей (open_idx, core_start, core_end, close_idx) — позиции ядра
    в кавычках. core = norm[core_start:core_end]."""
    out = []
    for m in _QUOTED_RE.finditer(norm):
        gi = next(i for i in range(1, 5) if m.group(i) is not None)
        out.append((m.start(), m.start(gi), m.end(gi), m.end()))
    return out


# --------------------------------------------------------------------------- #
#         Закрытый ЮРИДИЧЕСКИЙ класс организационно-правовых форм              #
# --------------------------------------------------------------------------- #
# Это ГРАММАТИКА ЖАНРА, а не стоп-словарь названий: перечень org-ПРАВОВЫХ форм
# конечен и закрыт. Работает по НОРМАЛИЗОВАННОМУ тексту (омоглифы/невидимые/разрывы
# сведены в anchor_search_view ДО детектора), включая искажения в самих формах.
#
# ИП НАМЕРЕННО ИСКЛЮЧЁН из org-форм. В корпусе «ИП + ФИО» размечен как PER (нота
# gold «составная форма: ИП+ФИО»), а не ORG: индивидуальный предприниматель — это
# ЧЕЛОВЕК. Ставить ИП в org-форму значило бы перетипизировать человека в организацию
# и отобрать у него PER-токен. Поэтому ORG-якорь на ИП/«индивидуальный
# предприниматель» не строится (их обрабатывает PER-детекция + syntax_compound как
# прежде).
# Дворово-садовые/жилищные формы (СНТ/ТСЖ/ТСН/ЖСК) НАМЕРЕННО исключены: в корпусе они
# встречаются как маркер МЕСТА внутри адреса («тер. СНТ «Родник»»), а не как компания-
# контрагент. Якорить их как ORG значило бы вырезать компонент адреса в ORG-токен и
# обнажать остаток адреса (пере-фрагментация). КФХ (крестьянское фермерское хозяйство)
# ОСТАВЛЕН: это бизнес-контрагент во главе с человеком, в адресах не встречается.
_ABBR_FORMS = (
    "ооо", "оао", "пао", "зао", "ао", "нко", "ано", "пк", "нао",
    "фгуп", "муп", "гуп", "фгбу", "гбу", "мбу", "кфх",
)
_ABBR_ALT = "|".join(_ABBR_FORMS)
_ABBR_FORM_RE = re.compile(r"(?i)(?<![а-яёa-z])(?:%s)(?![а-яёa-z])" % _ABBR_ALT)

# Развёрнутые формы юрлиц (фразы, стемы), БЕЗ «предпринимател» (см. про ИП выше).
# Фраза может занимать несколько слов («общество с ограниченной ответственностью»)
# — поэтому не просто стем, а развёртка с необязательным хвостом.
_SPELLED_PHRASE = (
    r"обществ\w*(?:\s+с\s+ограниченн\w*\s+ответственност\w*)?"
    r"|(?:публичн\w*\s+|непубличн\w*\s+)?акционерн\w*(?:\s+обществ\w*)?"
    r"|товариществ\w*|производственн\w*\s+кооператив\w*|кооператив\w*"
    r"|унитарн\w*(?:\s+предприят\w*)?"
    r"|некоммерческ\w*\s+(?:организац\w*|партн[её]рств\w*)|ассоциац\w*"
    r"|ограниченн\w*\s+ответственност\w*|ответственност\w*"
)
_SPELLED_FORM_RE = re.compile(r"(?i)(?:%s)" % _SPELLED_PHRASE)

# org-форма (аббревиатура ИЛИ развёрнутая фраза) как самостоятельный матч —
# для прохода «форма + голое имя» (пАттерн B: «ПАО Сбербанк», «ООО Медведев»).
_ORGFORM_ANY_RE = re.compile(
    r"(?<![а-яёa-z])(?:%s)(?![а-яёa-z])|(?:%s)" % (_ABBR_ALT, _SPELLED_PHRASE),
    re.IGNORECASE,
)
# та же форма, но ЗАКАНЧИВАЮЩАЯСЯ на конце строки (для проверки «форма вплотную
# слева от кавычки»).
_ORGFORM_END_RE = re.compile(
    r"(?:(?<![а-яёa-z])(?:%s)|(?:%s))$" % (_ABBR_ALT, _SPELLED_PHRASE),
    re.IGNORECASE,
)

# Окно поиска org-формы слева от кавычки: форма обязана ЗАКАНЧИВАТЬСЯ вплотную (лишь
# пробелы) перед открывающей кавычкой. Окно ограничивает длину развёрнутой фразы.
_FORM_LEFT_WINDOW = 45


def _orgform_start_left(low: str, open_idx: int):
    """Если org-форма заканчивается ВПЛОТНУЮ (только пробелы) перед позицией
    open_idx — вернуть индекс НАЧАЛА этой формы, иначе None. Требование
    непосредственного примыкания — защита от ложной привязки: «ПАО «ПромСнаб»,
    именуемое в дальнейшем «Агент»» не должно цеплять «ПАО» к алиасу «Агент»
    (между ними целая фраза, форма не примыкает к «Агент»)."""
    win_start = max(0, open_idx - _FORM_LEFT_WINDOW)
    left = low[win_start:open_idx]
    cut = max(left.rfind("."), left.rfind("!"), left.rfind("?"),
              left.rfind("\n"), left.rfind(";"), left.rfind(","))
    if cut != -1:
        win_start += cut + 1
        left = low[win_start:open_idx]
    stripped = left.rstrip()
    if not stripped:
        return None
    m = _ORGFORM_END_RE.search(stripped)
    if m:
        return win_start + m.start()
    return None


def _orgform_left(low: str, open_idx: int) -> bool:
    return _orgform_start_left(low, open_idx) is not None


# --------------------------------------------------------------------------- #
#                   Токены-«имена» (проходные для якоря и прохода 2)           #
# --------------------------------------------------------------------------- #
# Слово: буквы (кир/лат, уже сведённые), допускаем внутренний дефис («Ромашка-Плюс»)
# и цифры внутри («Строй2000») — но токен обязан начинаться с буквы.
_WORD_RE = re.compile(r"[^\W\d_][\w\-]*", re.UNICODE)

# Инициалы «И.», «И.О.» — часть ФИО, НЕ имени организации. Нужны, чтобы name-run
# перед ИНН не грёб «Соколова О. М.» (это человек-ИП) как имя юрлица.
_INITIALS_RE = re.compile(r"^[А-ЯЁ]\.$")


def _is_name_word(w: str) -> bool:
    """Слово похоже на элемент имени собственного: длиной >=2 и НЕ целиком в верхнем
    регистре. Отсекает метки реквизитов и аббревиатуры (ИНН/ОГРН/КПП/РФ) от name-run.
    ВАЖНО: настоящие ALL-CAPS названия к этому моменту уже приведены к Titlecase
    нормализацией регистра extractor'а (detection_text), поэтому «ПЕТРОВ КОНСАЛТИНГ»
    проходит как «Петров Консалтинг», а метка «ИНН» в смешанном тексте — нет."""
    core = w.strip("-")
    return len(core) >= 2 and not core.isupper()


def _lemma_first(word: str) -> str:
    """Каноническая лемма слова (наиболее вероятная нормальная форма, нижний
    регистр). pymorphy на незнакомом имени возвращает само слово в нижнем
    регистре («ПромСнаб»→«промснаб»)."""
    forms = _MORPH.normal_forms(word)
    return forms[0].lower() if forms else word.lower()


def _lemma_set(word: str) -> frozenset:
    """Все нормальные формы слова (нижний регистр). Множество — чтобы падежный
    омоним совпал с ключом («Заре»→{заря,зара} ∋ ключ «заря»)."""
    return frozenset(f.lower() for f in _MORPH.normal_forms(word)) or frozenset({word.lower()})


def _core_key(core_text: str):
    """Ключ реестра из текста ядра: кортеж канонических лемм слов ядра. Многословное
    ядро («Логистик Групп») даёт многоэлементный ключ; служебные слова («дом» в
    «Торговый дом «X»») в кавычки обычно не попадают, а если попали — остаются
    частью ключа (сопоставление прохода 2 всё равно точное, по всей
    последовательности)."""
    words = _WORD_RE.findall(core_text)
    words = [w for w in words if not _INITIALS_RE.match(w)]
    if not words:
        return None
    return tuple(_lemma_first(w) for w in words)


# --------------------------------------------------------------------------- #
#            Морфология ИМЕНИ СОБСТВЕННОГО (PER) — этап B                      #
# --------------------------------------------------------------------------- #
# Фамилия/имя/отчество определяются СТРУКТУРНО, по грамматическим пометам pymorphy
# (Surn/Name/Patr), а НЕ по словарю фамилий (запрет ТЗ). Тот же _MORPH, что уже
# загружен под Natasha. Пометы дают падеж/род «бесплатно», поэтому косвенный падеж
# («Морозовой Анне Николаевне») собирается в ОДИН спан без опоры на границы Natasha.
_INITIAL_RE = re.compile(r"^[А-ЯЁA-Z]$")   # одиночная заглавная буква = инициал


# Порог правдоподобия помету имени. pymorphy держит МУСОРНЫЕ разборы с околонулевым
# счётом: «по» имеет разбор-фамилию «По» (Эдгар По) со счётом 8.9e-05, «о»/«с»/«в» —
# тоже. Без порога предлог «по» метится PER и тянет за собой хвост ФИО-прогона.
# Настоящие части имени идут со счётом >=0.125 (Анне 0.125, Ивановой 0.125, Мороз
# 0.27), поэтому порог 0.01 отсекает мусор, не задевая ни одной реальной формы.
_NAMEPART_MIN_SCORE = 0.01


def _nameparts(word: str) -> frozenset:
    """Множество ролей ФИО, которые pymorphy приписывает слову с ПРАВДОПОДОБНЫМ счётом:
    {'surn','name','patr'}. Пустое — слово не часть имени собственного (по морфологии)."""
    tags = set()
    for f in _MORPH(word):
        if f.score < _NAMEPART_MIN_SCORE:
            continue
        t = f.tag
        if "Surn" in t:
            tags.add("surn")
        if "Name" in t:
            tags.add("name")
        if "Patr" in t:
            tags.add("patr")
    return frozenset(tags)


def _is_initial(word: str) -> bool:
    return _INITIAL_RE.match(word) is not None


# Максимум РЕАЛЬНЫХ слов-имён (не инициалов) в одном ФИО-спане — защита от
# «убегания» прогона по соседним фамилиям в перечислении.
_FIO_MAX_WORDS = 4


def _fio_run(norm, tokens, npar, i):
    """Собирает СВЯЗНЫЙ спан ФИО вокруг токена i (у которого есть помета имени).
    Влево и вправо присоединяет соседние токены-части имени (surn/name/patr) и
    инициалы, если между ними только пробелы/точки. Возвращает (lo, hi) — диапазон
    ИНДЕКСОВ токенов [lo, hi], целиком составляющих ФИО."""
    lo = hi = i
    real = 1 if not _is_initial(tokens[i].group(0)) else 0
    # вправо
    while hi + 1 < len(tokens):
        gap = norm[tokens[hi].end():tokens[hi + 1].start()]
        if gap.strip(" \t."):
            break
        w = tokens[hi + 1].group(0)
        is_init = _is_initial(w)
        if not (is_init or npar[hi + 1]):
            break
        if not is_init and real >= _FIO_MAX_WORDS:
            break
        hi += 1
        if not is_init:
            real += 1
    # влево
    while lo - 1 >= 0:
        gap = norm[tokens[lo - 1].end():tokens[lo].start()]
        if gap.strip(" \t."):
            break
        w = tokens[lo - 1].group(0)
        is_init = _is_initial(w)
        if not (is_init or npar[lo - 1]):
            break
        if not is_init and real >= _FIO_MAX_WORDS:
            break
        lo -= 1
        if not is_init:
            real += 1
    return lo, hi


def _fio_span(norm, tokens, lo, hi):
    """Символьные границы ФИО-прогона [lo,hi]. Если прогон заканчивается инициалом,
    включаем завершающую точку («Беляев О. О.» целиком, с точкой) — так граница
    совпадает с эталонной формой корпуса."""
    s = tokens[lo].start()
    e = tokens[hi].end()
    if _is_initial(tokens[hi].group(0)) and norm[e:e + 1] == ".":
        e += 1
    return s, e


def _surname_key(tokens, npar, lo, hi):
    """Ключ реестра для ФИО-спана [lo,hi]: лемма ФАМИЛИИ (первый токен с пометой
    'surn'). Возвращает (key_tuple, surname_token_index) или (None, None), если
    фамилии в прогоне нет (голое имя/отчество якорем PER не считаем)."""
    for j in range(lo, hi + 1):
        if "surn" in npar[j]:
            return (_lemma_first(tokens[j].group(0)),), j
    return None, None


# Маркеры-контекст, при которых даже ГОЛАЯ фамилия анкорится как человек. Проверяются
# в левом окне перед прогоном ФИО (омоглифы/регистр уже сведены anchor_search_view).
_PER_GRAZHD_RE = re.compile(r"(?i)(?:граждан(?:ин|ка)|\bгр\.)\s*$")
# Роль-слово в левом окне: допускаем одно промежуточное слово (org-форму) между
# ролью и ФИО — «Глава КФХ Петров», «Генеральный директор ООО Иванов И.И.» (A-3).
_PER_ROLE_RE = re.compile(
    r"(?i)(?:директор\w*|бухгалтер\w*|руководител\w*|президент\w*|глав\w*|начальник\w*"
    r"|управляющ\w*|представител\w*|учредител\w*|нотариус\w*|председател\w*).{0,15}$"
)
_PER_VLICE_RE = re.compile(r"(?i)в\s+лице\s*$")
_PER_IP_RE = re.compile(
    r"(?i)(?:индивидуальн\w*\s+предпринимател\w*|(?<![а-яёa-z])ип)\s*$"
)
# «действующ…» после ФИО — подтверждение конструкции «в лице X, действующего…».
_PER_ACTING_RE = re.compile(r"(?i)^[\s,]*действу\w+")

# Адресный тип-маркер ВПЛОТНУЮ слева от прогона: «ул. Пушкина», «ш. Гагарина»,
# «г. Пушкин» — топоним/улица-однофамилец, НЕ человек. Метить PER здесь нельзя
# (коллизия фамилия↔улица; улица — компонент адреса, этап C). Закрытый список
# типов места, примыкающих точкой/пробелом к имени собственному.
_PER_ADDR_TYPE_LEFT_RE = re.compile(
    r"(?i)(?:^|[\s,.])(?:ул|улиц\w*|пер|переул\w*|пр|пр-?кт|пр-?т|проспект\w*|ш|шоссе"
    r"|наб|набережн\w*|б-?р|бульвар\w*|проезд\w*|пл|площад\w*|туп|тупик\w*|алле\w*"
    r"|г|гор|город\w*|д|дер|деревн\w*|с|село|пос|пгт|мкр|мкрн|р-?н|обл|область|кра\w*"
    r"|респ|округ\w*|кв|кварта\w*|тер|снт|днт)\.?\s{0,2}$"
)


def _addr_type_left(low: str, s: int) -> bool:
    """True, если ВПЛОТНУЮ слева от позиции s стоит адресный тип-маркер (улица/город/
    и т.п.). Тогда следующее имя собственное — топоним, не человек."""
    return _PER_ADDR_TYPE_LEFT_RE.search(low[max(0, s - 14):s]) is not None
# Инициалы вокруг фамилии — усиливающий признак (И. О. / И.О.).


# --------------------------------------------------------------------------- #
#                         Реестр документа (Registry)                         #
# --------------------------------------------------------------------------- #
@dataclass
class RegistryRecord:
    key: tuple
    entity_type: str
    canonical: str
    evidence: set = field(default_factory=set)
    confidence: int = 0


class Registry:
    """Ключ (лемма ядра) → RegistryRecord. Арбитраж конфликтов типов — ЗДЕСЬ, при
    записи. В прототипе тип один (ORG), но интерфейс арбитража заложен: при
    коллизии ключа с РАЗНЫМИ типами побеждает бóльшая уверенность (B наполнит
    правило)."""

    def __init__(self):
        self._by_key: dict[tuple, RegistryRecord] = {}

    def add(self, key, entity_type, canonical, evidence, confidence):
        rec = self._by_key.get(key)
        if rec is None:
            self._by_key[key] = RegistryRecord(
                key=key, entity_type=entity_type, canonical=canonical,
                evidence=set(evidence), confidence=confidence,
            )
            return
        # ключ уже есть.
        if rec.entity_type == entity_type:
            rec.evidence |= set(evidence)
            rec.confidence = max(rec.confidence, confidence)
        else:
            # АРБИТРАЖ ТИПОВ: сильнейшая уверенность выигрывает тип ключа.
            if confidence > rec.confidence:
                rec.entity_type = entity_type
                rec.canonical = canonical
                rec.evidence = set(evidence)
                rec.confidence = confidence

    def get(self, key):
        return self._by_key.get(key)

    def keys(self):
        return self._by_key.keys()

    def records(self):
        return list(self._by_key.values())


# --------------------------------------------------------------------------- #
#                          Anchor + интерфейс детектора                        #
# --------------------------------------------------------------------------- #
@dataclass
class Anchor:
    seg_id: str
    span_start: int          # норм-координаты полного упоминания (форма+ядро)
    span_end: int
    core_key: tuple
    canonical: str
    evidence: set
    confidence: int


class AnchorDetector:
    """Pluggable-интерфейс детектора якорей ОДНОГО типа. Реализация обязана дать
    entity_type и detect(...). guard(...) переопределяется под коллизии типа."""

    entity_type = None

    def search_view(self, seg):
        """Поисковый вид сегмента (norm, offset_map→segment.text) для ЭТОГО типа.
        ORG вырезает перенос строки (однотокенный ключ), PER заменяет его пробелом
        (многотокенный ключ) — см. per_search_view/_strip_breaking."""
        return anchor_search_view(seg)

    def detect(self, seg, norm, low, omap, requisites) -> list:
        """requisites — список (ns, ne, kind, digit_count) валидированных реквизитов
        сегмента (INN/OGRN/SNILS) в координатах anchor_search_view. Детектор сам
        решает, какие ему нужны (ORG: ИНН-10/ОГРН-13 юрлица; PER: ИНН-12/СНИЛС)."""
        raise NotImplementedError

    def guard(self, norm, low, run_start, run_end) -> bool:
        raise NotImplementedError

    def expand_match(self, norm, low, tokens, npar, i, klen):
        """Проход 2: по какому диапазону символов пометить вхождение ключа, начавшееся
        с токена i длиной klen. По умолчанию — ровно эти klen токенов (поведение ORG).
        PER переопределяет: расширяет до СВЯЗНОГО спана ФИО (косвенный падеж целиком)."""
        return tokens[i].start(), tokens[i + klen - 1].end()


class OrgAnchorDetector(AnchorDetector):
    """Детектор якорей ORG. Признаки работают ПО СОВОКУПНОСТИ (evidence-множество,
    больше сошлось = выше confidence): org-форма рядом, имя в кавычках, конструкция
    введения, соседство с валидированным ИНН(10)/ОГРН(13) ЮРЛИЦА."""

    entity_type = "ORG"

    # Конструкция введения: «далее — «X»», «именуемое в дальнейшем …» и падежные
    # варианты именуем-. Здесь важно ЛЕВОЕ имя (то, что ВВОДИТСЯ), а не алиас после.
    _INTRO_RE = re.compile(r"(?i)(?:именуем\w+|\bдалее\b|в\s+дальнейшем)")

    # Роль-слово перед org-формой («Глава КФХ …», A-3): следующее за формой имя —
    # ЧЕЛОВЕК-руководитель, а НЕ часть названия юрлица. Тогда pattern B не грабит имя
    # в ORG-блоб, имя достаётся PER-детектору по роль-маркеру.
    _ROLE_BEFORE_FORM_RE = re.compile(
        r"(?i)(?:глав\w*|директор\w*|руководител\w*|начальник\w*|председател\w*)\W{0,8}$"
    )

    def detect(self, seg, norm, low, omap, requisites) -> list:
        # ЮРЛИЦО-реквизиты для якоря ORG: ИНН из 10 цифр и ОГРН из 13 цифр.
        # 12-значный ИНН / 15-значный ОГРНИП — физлицо/ИП (работают на PER).
        inn_ogrn_norm = [
            (ns, kind) for (ns, ne, kind, dc) in requisites
            if (kind == "INN" and dc == 10) or (kind == "OGRN" and dc == 13)
        ]
        anchors: list[Anchor] = []

        # --- Признак: имя в кавычках (+ org-форма слева / конструкция введения) ---
        for (oq, cs, ce, cq) in _find_quoted(norm):
            core = norm[cs:ce].strip()
            key = _core_key(core)
            if key is None:
                continue
            form_start = _orgform_start_left(low, oq)
            if form_start is None:
                # Кавычки САМИ ПО СЕБЕ не регистрируют: «Договор»/«Приложение»/алиасы
                # «Агент»/«Цедент» тоже в кавычках. Регистрируем ядро, только если
                # org-форма ПРИМЫКАЕТ слева (защита от генериков).
                continue
            ev = {"quotes", "orgform"}
            tail = low[cq:cq + 25]
            if self._INTRO_RE.match(tail.lstrip(" (—-,")):
                ev.add("intro")
            anchors.append(Anchor(
                seg.id, form_start, cq, key, core, ev, len(ev),
            ))

        # --- Признак: org-форма + ГОЛОЕ имя без кавычек (пАттерн B) ---
        # «ПАО Сбербанк», «ООО Медведев», «ПАО Василёк» — в корпусе часть ORG-имён
        # без кавычек. Форма примыкает слева, имя — прогон заглавных справа.
        for m in _ORGFORM_ANY_RE.finditer(low):
            j = m.end()
            while j < len(norm) and norm[j] in " \t":
                j += 1
            if j >= len(norm) or norm[j] in _OPEN_QUOTES + _ASCII_QUOTE:
                continue  # за формой кавычка — это кавычный случай выше
            # A-3: роль-слово слева от формы («Глава КФХ …») — имя за формой это
            # РУКОВОДИТЕЛЬ (человек), не часть названия. Не грабим его в ORG-блоб;
            # PER-детектор возьмёт его по роль-маркеру. Саму форму как ORG тоже не
            # регистрируем (голая «КФХ» без имени не идентифицирует организацию).
            if self._ROLE_BEFORE_FORM_RE.search(low[:m.start()]):
                continue
            # После АББРЕВИАТУРНОЙ формы допускаем ведущий ALL-CAPS токен имени
            # («АО НПО …», «КБ …»): сразу за «АО»/«ПАО» метки реквизита не стоят, а
            # имя-абброс организации частотно. За РАЗВЁРНУТОЙ формой (Общество…) —
            # нет: там ALL-CAPS следующего слова чаще ложный (защита от прозы).
            abbr = _ABBR_FORM_RE.fullmatch(m.group(0).lower()) is not None
            run = self._name_run_forward(norm, low, j, allow_caps=abbr)
            if run is None:
                continue
            rs, re_ = run
            key = _core_key(norm[rs:re_])
            if key is None:
                continue
            anchors.append(Anchor(
                seg.id, m.start(), re_, key, norm[rs:re_].strip(),
                {"orgform"}, 1,
            ))

        # --- Признак: соседство с валидированным ИНН(10)/ОГРН(13) юрлица ---
        # Name-run слева от реквизита юрлица. 12-значный ИНН / 15-значный ОГРНИП —
        # это ФИЗЛИЦО/ИП, НЕ юрлицо: по ним якорь ORG не строим (см. про ИП).
        for (num_start, kind) in inn_ogrn_norm:
            run = self._name_run_before_requisite(norm, low, num_start)
            if run is None:
                continue
            rs, re_ = run
            key = _core_key(norm[rs:re_])
            if key is None:
                continue
            anchors.append(Anchor(
                seg.id, rs, re_, key, norm[rs:re_].strip(),
                {kind}, 1,
            ))

        # Конструкция введения как САМОСТОЯТЕЛЬНЫЙ якорь («X, именуемое …» без
        # кавычек и без org-формы) НАМЕРЕННО не строится: единственное её применение
        # (юрлицо без формы/кавычек) в корпусе всегда сопровождается ИНН юрлица
        # рядом (признак C), а «X» с формой/кавычками ловит признак A. Отдельный
        # проход по «именуем…» цеплял метку реквизита («ИНН») и одиночные буквы у
        # ИП-персон («ИП Макаров …, ИНН …, именуемый «Работодатель»»). Введение
        # осталось лишь как ДОКАЗАТЕЛЬСТВО (evidence 'intro') кавычного якоря выше.
        return anchors

    # ---- вспомогательные ----
    def _name_run_forward(self, norm, low, start, allow_caps=False):
        """Прогон заглавных слов ВПРАВО от позиции start (после org-формы). Первое
        слово обязано начинаться ровно на start и с заглавной. Инициал/строчное
        слово/аббревиатура-форма/цифра обрывают прогон. Не более 3 слов.
        allow_caps — разрешить ВЕДУЩИЙ ALL-CAPS токен (org-абброс «НПО»); нужен после
        аббревиатурной формы."""
        if start >= len(norm) or not norm[start:start + 1].isupper():
            return None
        run = []
        for mm in _WORD_RE.finditer(norm, start):
            if not run and mm.start() != start:
                return None
            if run:
                gap = norm[run[-1].end():mm.start()]
                if gap.strip(" \t"):
                    break
            w = mm.group(0)
            if _INITIALS_RE.match(w) or not w[:1].isupper():
                break
            if _ABBR_FORM_RE.fullmatch(w.lower()):
                break
            # ведущий ALL-CAPS абброс допускаем только как ПЕРВОЕ слово при allow_caps
            if not _is_name_word(w) and not (allow_caps and not run and len(w) >= 2):
                break
            run.append(mm)
            if len(run) >= 3:
                break
        if not run:
            return None
        return run[0].start(), run[-1].end()

    def _name_run_before_requisite(self, norm, low, num_start):
        """Name-run слева от ИНН/ОГРН-числа: пропускаем пробелы, метку
        «инн/огрн», снова пробелы — и собираем прогон заглавных слов. Если прогону
        непосредственно предшествует «ип» — это ИП (человек), пропускаем."""
        i = num_start
        # пропустить пробелы
        while i > 0 and norm[i - 1] in " \t":
            i -= 1
        # пропустить метку реквизита (инн/огрн, регистр/омоглифы уже сведены)
        mlab = re.search(r"(?i)(инн|огрн)\s*$", low[:i])
        if not mlab:
            return None
        i = mlab.start()
        while i > 0 and norm[i - 1] in " \t":
            i -= 1
        return self._collect_name_run(norm, low, i)

    def _collect_name_run(self, norm, low, end):
        """Собирает влево прогон заглавных слов, заканчивающийся на позиции end.
        Возвращает (start, end') или None. Останавливается на строчном слове,
        цифре-реквизите или org-форме (её саму в имя не берём — но раз она есть,
        это уже покрыто кавычным признаком)."""
        words = list(_WORD_RE.finditer(norm[:end]))
        if not words:
            return None
        run_words = []
        for m in reversed(words):
            w = m.group(0)
            # разрыв между этим словом и уже собранным должен быть только пробелы
            if run_words:
                gap = norm[m.end():run_words[-1].start()]
                if gap.strip(" \t"):
                    break
            if _INITIALS_RE.match(w):
                # инициал внутри name-run обрывает организацию (это ФИО-хвост)
                break
            if not w[:1].isupper() or not _is_name_word(w):
                break
            if _ABBR_FORM_RE.fullmatch(w.lower()) or _SPELLED_FORM_RE.fullmatch(w.lower()):
                break
            # «ип» слева от прогона => это ИП-человек, не юрлицо
            run_words.append(m)
        if not run_words:
            return None
        run_words.reverse()
        rs, re_ = run_words[0].start(), run_words[-1].end()
        # проверка «ип» непосредственно слева
        pre = low[:rs].rstrip(" \t")
        if re.search(r"(?<![а-яё])ип$", pre):
            return None
        return rs, re_

    # ---- guard коллизии леммы (проход 2) ----
    def guard(self, norm, low, s, e) -> bool:
        """ООО «Заря» vs «на заре». Точность важнее полноты: сомнение = не метить.
          а) в кавычках → метить;
          б) org-форма рядом слева → метить;
          в) с заглавной И не в начале предложения → метить;
          г) с заглавной И в начале предложения → нужен ВТОРОЙ сигнал (а/б/введение);
          д) строчная в середине предложения → НЕ метить."""
        in_quotes = (s > 0 and norm[s - 1] in _OPEN_QUOTES + _ASCII_QUOTE
                     and e < len(norm) and norm[e] in _CLOSE_QUOTES + _ASCII_QUOTE)
        if in_quotes:
            return True
        near_form = _orgform_left(low, s) or self._abbr_immediately_left(low, s)
        if near_form:
            return True
        capitalized = norm[s:s + 1].isupper()
        at_sent_start = self._at_sentence_start(norm, s)
        if capitalized and not at_sent_start:
            return True                       # (в)
        if capitalized and at_sent_start:
            return False                      # (г) без 2-го сигнала — не метить
        return False                          # (д) строчная в середине

    def _abbr_immediately_left(self, low, s):
        left = low[max(0, s - 8):s].rstrip(" \t\"«")
        return bool(_ABBR_FORM_RE.search(left[-5:])) if left else False

    @staticmethod
    def _at_sentence_start(norm, s):
        i = s - 1
        while i >= 0 and norm[i] in " \t\"«“„(":
            i -= 1
        if i < 0:
            return True
        return norm[i] in ".!?\n"


class PerAnchorDetector(AnchorDetector):
    """Детектор якорей PER (люди). Та же машина, что ORG: якорь → реестр по лемме
    ФАМИЛИИ → проход 2 по падежам. Признаки якоря (по совокупности):
      * САМО-ЯКОРЬ по форме ФИО: фамилия + имя/отчество ИЛИ фамилия + инициалы —
        структурно однозначная запись человека (99.7% ФИО корпуса, замер этапа B);
      * ГОЛАЯ фамилия анкорится ТОЛЬКО при внешнем маркере: «гражданин/гражданка/гр.»,
        должность рядом, «в лице …, действующего…», ИП, соседний ИНН-12/СНИЛС физлица.
    Безъякорная одиночная фамилия («…и Иванов подписал…») СОЗНАТЕЛЬНО пропускается
    (класс Aprime-1 для PER) — precision важнее recall.
    """

    entity_type = "PERSON"

    def search_view(self, seg):
        return per_search_view(seg)

    def detect(self, seg, norm, low, omap, requisites) -> list:
        req_phys = [
            (ns, ne) for (ns, ne, kind, dc) in requisites
            if kind == "SNILS" or (kind == "INN" and dc == 12)
        ]
        anchors: list[Anchor] = []
        tokens = list(_WORD_RE.finditer(norm))
        if not tokens:
            return anchors
        npar = [_nameparts(t.group(0)) for t in tokens]

        seen_spans: set = set()
        for i, t in enumerate(tokens):
            if "surn" not in npar[i]:
                continue
            lo, hi = _fio_run(norm, tokens, npar, i)
            key, si = _surname_key(tokens, npar, lo, hi)
            if key is None:
                continue
            s, e = _fio_span(norm, tokens, lo, hi)
            if (s, e) in seen_spans:
                continue
            seen_spans.add((s, e))

            # Адресный тип-маркер слева («ул. Пушкина», «ш. Гагарина») — это улица-
            # однофамилец, не человек: PER-якорь не строим (коллизия с ADDRESS, этап C).
            if _addr_type_left(low, s):
                continue

            # доказательства
            ev = set()
            real_words = sum(1 for j in range(lo, hi + 1)
                             if not _is_initial(tokens[j].group(0)))
            has_init = any(_is_initial(tokens[j].group(0)) for j in range(lo, hi + 1))
            has_given = any(("name" in npar[j] or "patr" in npar[j])
                            for j in range(lo, hi + 1))
            # само-якорь: фамилия + имя/отчество, либо фамилия + инициалы
            if (real_words >= 2 and has_given) or has_init:
                ev.add("fullname")

            left = low[max(0, s - 40):s]
            if _PER_GRAZHD_RE.search(left):
                ev.add("grazhd")
            if _PER_ROLE_RE.search(left):
                ev.add("role")
            if _PER_IP_RE.search(left):
                ev.add("ip")
            if _PER_VLICE_RE.search(left):
                ev.add("vlice")
            elif _PER_ACTING_RE.match(low[e:e + 30]) and \
                    re.search(r"(?i)в\s+лице\b", low[max(0, s - 60):s]):
                ev.add("vlice")
            # соседний реквизит физлица (в том же сегменте, окно 40 симв. по обе стороны)
            for (rns, rne) in req_phys:
                if abs(rns - e) <= 40 or abs(s - rne) <= 40:
                    ev.add("req")
                    break

            if not ev:
                continue   # голая фамилия без маркера — пропускаем (Aprime-1 PER)

            anchors.append(Anchor(
                seg.id, s, e, key, norm[s:e].strip(), ev, len(ev),
            ))

            # ВТОРИЧНЫЙ ключ (имя, отчество) от УВЕРЕННОГО полного ФИО (фамилия+имя+
            # отчество): нужен, когда продолжение ФИО оторвано в СОСЕДНИЙ сегмент
            # («…Сидоров» | «Михаил Михайлович» — cross-segment split, вне области
            # Entity.spans). Тогда проход 2 накрывает осиротевшее «Имя Отчество» по
            # этому ключу того же человека. Структурно (без словаря имён), внутри
            # документа. Регистрируется ТОЛЬКО от полной формы — не от голого имени.
            name_lemma = patr_lemma = None
            for j in range(lo, hi + 1):
                if "name" in npar[j] and name_lemma is None:
                    name_lemma = _lemma_first(tokens[j].group(0))
                if "patr" in npar[j] and patr_lemma is None:
                    patr_lemma = _lemma_first(tokens[j].group(0))
            if name_lemma and patr_lemma and "surn" in npar[si]:
                anchors.append(Anchor(
                    seg.id, s, e, (name_lemma, patr_lemma),
                    norm[s:e].strip(), ev | {"namepatr"}, len(ev),
                ))
        return anchors

    def expand_match(self, norm, low, tokens, npar, i, klen):
        """Проход 2: вокруг совпавшей фамилии собрать СВЯЗНЫЙ спан ФИО целиком —
        косвенный падеж («Морозовой Анне Николаевне») одним спаном (закрывает PER-B)."""
        lo, hi = _fio_run(norm, tokens, npar, i)
        return _fio_span(norm, tokens, lo, hi)

    def guard(self, norm, low, s, e) -> bool:
        """Коллизия «фамилия-омоним обычного слова» (Мороз/Зима). Точность важнее
        полноты. Метим вхождение уже ЗАРЕГИСТРИРОВАННОГО человека, если:
          * оно начинается с ЗАГЛАВНОЙ (строчное «мороз» в середине — обычное слово);
          * И это не одиночная заглавная в начале предложения без имени рядом.
        Расширенный проходом-2 спан (фамилия+имя/инициал) — само по себе второй
        признак, поэтому многословный спан метим всегда."""
        if not norm[s:s + 1].isupper():
            return False
        # адресный тип-маркер слева — улица-однофамилец, не человек (коллизия ADDRESS)
        if _addr_type_left(low, s):
            return False
        # многословный спан (есть пробел внутри) — форма ФИО, метим
        if " " in norm[s:e].strip() or "." in norm[s:e]:
            return True
        at_start = OrgAnchorDetector._at_sentence_start(norm, s)
        return not at_start


# --------------------------------------------------------------------------- #
#                               Движок                                         #
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"\d")


def _digit_count(text: str) -> int:
    return len(_NUM_RE.findall(text))


def _requisites_src_by_seg(regex_entities):
    """Реквизиты (ИНН/ОГРН/СНИЛС) в ИСХОДНЫХ координатах сегмента: (src_s, src_e,
    kind, digit_count). В координаты поискового вида КОНКРЕТНОГО детектора переводит
    движок (у ORG и PER разные виды, поэтому единой norm-координаты нет)."""
    out: dict[str, list] = {}
    for e in regex_entities or []:
        if e.entity_type not in ("INN", "OGRN", "SNILS"):
            continue
        dc = _digit_count(e.original_text)
        out.setdefault(e.segment_id, []).append((e.start, e.end, e.entity_type, dc))
    return out


class AnchorEngine:
    """Движок: pass-1 (якоря → реестр + сущности якорей), pass-2 (падежные вхождения
    по реестру + guard). Типо-параметричен: список detectors — по одному на тип."""

    def __init__(self, detectors):
        self.detectors = detectors

    def run(self, doc: SourceDocument, regex_entities=None) -> list[Entity]:
        req_src_by_seg = _requisites_src_by_seg(regex_entities)
        registry = Registry()
        # один детектор на тип — для прохода 2 (тип ключа арбитрируется реестром,
        # детектор выбирается по ПОБЕДИВШЕМУ типу, не по тому, кто первый нашёл).
        det_by_type = {d.entity_type: d for d in self.detectors}

        # Поисковый вид — СВОЙ у каждого детектора (ORG вырезает \n, PER заменяет
        # пробелом). Кэш по (seg_id, det.entity_type).
        view_cache: dict[tuple, tuple] = {}

        def view_for(det, seg):
            k = (seg.id, det.entity_type)
            v = view_cache.get(k)
            if v is None:
                norm, omap = det.search_view(seg)
                v = (norm, norm.lower(), omap)
                view_cache[k] = v
            return v

        def reqs_for(det, seg, omap):
            out = []
            for (ss, se, kind, dc) in req_src_by_seg.get(seg.id, []):
                ns, ne = src_to_norm(omap, ss, se)
                out.append((ns, ne, kind, dc))
            return out

        entities: list[Entity] = []
        # барьеры эмита — ОБЩИЕ для всех типов, но в РАЗНЫХ координатах у ORG и PER.
        # Храним занятые спаны в ИСХОДНЫХ координатах (общий язык) + переводим при
        # проверке пересечения.
        emitted_src: dict[str, list] = {}     # seg_id -> [(src_s,src_e)]

        # --- PASS 1a: СОБИРАЕМ все якоря (по всем детекторам, по всем сегментам) ---
        collected: list[tuple] = []   # (seg, det, norm, omap, Anchor)
        for seg in doc.segments:
            if not seg.text:
                continue
            for det in self.detectors:
                norm, low, omap = view_for(det, seg)
                reqs = reqs_for(det, seg, omap)
                for a in det.detect(seg, norm, low, omap, reqs):
                    collected.append((seg, det, norm, omap, a))

        # --- PASS 1b: АРБИТРАЖ ТИПОВ В РЕЕСТРЕ (один раз, порядко-независимо) ---
        # Сортируем по убыванию уверенности, чтобы сильнейшее доказательство задало
        # тип ключа независимо от порядка сегментов; при равной силе ORG вперёд PER
        # (структурный ORG «Восход» не перетипируется в PER — инвариант п.4 ТЗ).
        _type_rank = {"ORG": 0, "PERSON": 1}
        collected.sort(key=lambda c: (-c[4].confidence, _type_rank.get(c[1].entity_type, 9)))
        for (seg, det, norm, omap, a) in collected:
            registry.add(a.core_key, det.entity_type, a.canonical, a.evidence, a.confidence)

        # --- PASS 1c: ЭМИТ спанов якорей ПОБЕДИВШЕГО типа ---
        for (seg, det, norm, omap, a) in collected:
            rec = registry.get(a.core_key)
            if rec is None or rec.entity_type != det.entity_type:
                continue   # этот якорь проиграл арбитраж — эмитит победивший тип
            self._emit(entities, emitted_src, seg, norm, omap,
                       a.span_start, a.span_end, det.entity_type)

        # --- PASS 2: падежные вхождения по реестру + guard ---
        for seg in doc.segments:
            if not seg.text:
                continue
            for det in self.detectors:
                recs = [r for r in registry.records() if r.entity_type == det.entity_type]
                if not recs:
                    continue
                norm, low, omap = view_for(det, seg)
                tokens = list(_WORD_RE.finditer(norm))
                if not tokens:
                    continue
                lemsets = [_lemma_set(t.group(0)) for t in tokens]
                npar = [_nameparts(t.group(0)) for t in tokens] \
                    if det.entity_type == "PERSON" else None
                for rec in recs:
                    self._match_key(entities, emitted_src, seg, norm, low, omap,
                                    tokens, lemsets, npar, rec, det)

        return entities

    def _match_key(self, entities, emitted_src, seg, norm, low, omap,
                   tokens, lemsets, npar, rec, det):
        klen = len(rec.key)
        n = len(tokens)
        i = 0
        while i + klen <= n:
            ok = all(rec.key[j] in lemsets[i + j] for j in range(klen))
            if not ok:
                i += 1
                continue
            s, e = det.expand_match(norm, low, tokens, npar, i, klen)
            src_s, src_e = norm_to_src(omap, s, e)
            if self._covered(emitted_src, seg.id, src_s, src_e):
                i += klen
                continue
            if not det.guard(norm, low, s, e):
                i += klen
                continue
            self._emit(entities, emitted_src, seg, norm, omap, s, e,
                       rec.entity_type)
            i += klen

    @staticmethod
    def _covered(emitted_src, seg_id, s, e):
        for (a, b) in emitted_src.get(seg_id, ()):
            if max(a, s) < min(b, e):
                return True
        return False

    @staticmethod
    def _emit(entities, emitted_src, seg, norm, omap, ns, ne, entity_type):
        # обрезаем крайние пробелы/кавычки-скобки норм-спана
        while ns < ne and norm[ns] in " \t":
            ns += 1
        while ne > ns and norm[ne - 1] in " \t":
            ne -= 1
        if ns >= ne:
            return
        src_start, src_end = norm_to_src(omap, ns, ne)
        # межтиповая проверка занятости — в ИСХОДНЫХ координатах (у ORG и PER разные
        # norm-виды, общий язык — src). Иначе ORG- и PER-спаны не «видят» друг друга.
        if AnchorEngine._covered(emitted_src, seg.id, src_start, src_end):
            return
        entities.append(Entity(
            id=str(uuid.uuid4()),
            segment_id=seg.id,
            start=src_start,
            end=src_end,
            original_text=seg.text[src_start:src_end],
            entity_type=entity_type,
            detector="ner",
            confidence=1.0,
        ))
        emitted_src.setdefault(seg.id, []).append((src_start, src_end))


# --------------------------------------------------------------------------- #
#                     Арбитраж типов на выходе движка                          #
# --------------------------------------------------------------------------- #
def suppress_conflicts(org_entities, ner_entities):
    """Арбитраж КОНФЛИКТА ТИПОВ: убирает Natasha-сущность PER/ADDRESS, которая
    (1) ЦЕЛИКОМ покрыта ORG-сущностью движка И (2) все её содержательные леммы —
    подмножество лемм ЯДРА этой ORG. Это тот самый «Восход», что Natasha в реальной
    прозе рвёт на PER/ADDR/ORG: реестр — источник истины по ORG, его тип побеждает.
    PER/ADDRESS-ДЕТЕКТОРЫ не трогаются — решение на уровне арбитража.

    ДВА условия вместе — гарантия БЕЗ утечки:
      • «целиком покрыта» → удалённый спан всё равно замаскирован покрывающим ORG,
        значит удаление не может обнажить текст (иначе снимали бы маску впустую);
      • «леммы ⊆ ядро» → снимаем только НАСТОЯЩЕЕ ядро организации, а не человека,
        случайно делящего фамилию с фирмой («Макаров В. В.» ⊄ ядро «Макаров
        Консалтинг», раз у него есть лемма-инициал — впрочем инициалы отброшены, но
        полное ФИО «Макаров Владимир Владимирович» точно не подмножество).
    """
    by_seg: dict[str, list] = {}
    for oe in org_entities:
        by_seg.setdefault(oe.segment_id, []).append(oe)
    if not by_seg:
        return ner_entities

    # леммы ЯДРА каждой ORG-сущности (её собственного текста), считаем по разу
    org_core_lemmas = {id(oe): _entity_lemmas(oe) for oes in by_seg.values() for oe in oes}

    kept = []
    for e in ner_entities:
        if e.entity_type not in ("PERSON", "ADDRESS"):
            kept.append(e)
            continue
        oes = by_seg.get(e.segment_id)
        if not oes:
            kept.append(e)
            continue
        ent_lemmas = _entity_lemmas(e)
        drop = False
        for oe in oes:
            covered = oe.start <= e.start and e.end <= oe.end
            if covered and ent_lemmas and ent_lemmas <= org_core_lemmas[id(oe)]:
                drop = True
                break
        if not drop:
            kept.append(e)
    return kept


def _entity_lemmas(e):
    s = set()
    for w in _WORD_RE.findall(e.original_text):
        if _INITIALS_RE.match(w):
            continue
        s |= _lemma_set(w)
    return s


# --------------------------------------------------------------------------- #
#                            Публичная функция                                #
# --------------------------------------------------------------------------- #
def detect_org(doc: SourceDocument, regex_entities=None):
    """Только ORG-сущности (якоря + падежные вхождения). Оставлена для обратной
    совместимости отдельных тестов ORG; боевой конвейер зовёт detect_structural."""
    engine = AnchorEngine([OrgAnchorDetector()])
    return engine.run(doc, regex_entities=regex_entities)


def detect_structural(doc: SourceDocument, regex_entities=None):
    """Этап B: ORG + PER одним движком (один реестр → взаимный арбитраж типов п.4:
    «Восход» не станет PER, зарегистрированная фамилия не станет ORG). Возвращает
    смешанный список ORG- и PERSON-сущностей. ORG и PER взаимно НЕ поглощаются
    (emitted_spans движка), оба служат барьером адресу (см. detect_ner)."""
    engine = AnchorEngine([OrgAnchorDetector(), PerAnchorDetector()])
    return engine.run(doc, regex_entities=regex_entities)
