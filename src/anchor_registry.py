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
from normalizer import detection_view, norm_to_src, src_to_norm

# pymorphy-анализатор берём тот же, что уже загружен под Natasha (см. ТЗ: «pymorphy
# уже есть под Наташей»). Это НЕ обращение к NER Natasha — только морфология лемм.
from ner_detector import _morph_vocab as _MORPH


# --------------------------------------------------------------------------- #
#                     Нормализация кавычек (ЛОКАЛЬНО в детекторе)              #
# --------------------------------------------------------------------------- #
# В общий пайплайн нормализация кавычек не входит, поэтому она ЗДЕСЬ (требование
# ТЗ). Замена строго 1:1 по символам (длина сохраняется) — поэтому спаны,
# найденные в норм-тексте, остаются валидными координатами detection_view.
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
# конечен и закрыт. Работает по НОРМАЛИЗОВАННОМУ тексту (омоглифы/невидимые сведены
# в detection_view ДО детектора), включая искажения в самих формах.
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

    def detect(self, seg, norm, low, omap, inn_ogrn_norm) -> list:
        raise NotImplementedError

    def guard(self, norm, low, run_start, run_end) -> bool:
        raise NotImplementedError


class OrgAnchorDetector(AnchorDetector):
    """Детектор якорей ORG. Признаки работают ПО СОВОКУПНОСТИ (evidence-множество,
    больше сошлось = выше confidence): org-форма рядом, имя в кавычках, конструкция
    введения, соседство с валидированным ИНН(10)/ОГРН(13) ЮРЛИЦА."""

    entity_type = "ORG"

    # Конструкция введения: «далее — «X»», «именуемое в дальнейшем …» и падежные
    # варианты именуем-. Здесь важно ЛЕВОЕ имя (то, что ВВОДИТСЯ), а не алиас после.
    _INTRO_RE = re.compile(r"(?i)(?:именуем\w+|\bдалее\b|в\s+дальнейшем)")

    def detect(self, seg, norm, low, omap, inn_ogrn_norm) -> list:
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


# --------------------------------------------------------------------------- #
#                               Движок                                         #
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"\d")


def _digit_count(text: str) -> int:
    return len(_NUM_RE.findall(text))


def _inn_ogrn_norm_by_seg(regex_entities, doc):
    """Для каждого сегмента — позиции НАЧАЛА валидированных реквизитов ЮРЛИЦА в
    НОРМ-координатах: ИНН из 10 цифр и ОГРН из 13 цифр. 12-значный ИНН и
    15-значный ОГРНИП (физлицо/ИП) исключены."""
    out: dict[str, list] = {}
    seg_by_id = {s.id: s for s in doc.segments}
    for e in regex_entities or []:
        if e.entity_type not in ("INN", "OGRN"):
            continue
        dc = _digit_count(e.original_text)
        if e.entity_type == "INN" and dc != 10:
            continue
        if e.entity_type == "OGRN" and dc != 13:
            continue
        seg = seg_by_id.get(e.segment_id)
        if seg is None:
            continue
        _norm, omap = detection_view(seg)
        ns, _ne = src_to_norm(omap, e.start, e.end)
        out.setdefault(e.segment_id, []).append((ns, e.entity_type))
    return out


class AnchorEngine:
    """Движок: pass-1 (якоря → реестр + сущности якорей), pass-2 (падежные вхождения
    по реестру + guard). Типо-параметричен: список detectors — по одному на тип."""

    def __init__(self, detectors):
        self.detectors = detectors

    def run(self, doc: SourceDocument, regex_entities=None) -> list[Entity]:
        inn_by_seg = _inn_ogrn_norm_by_seg(regex_entities, doc)
        registry = Registry()

        # общий кеш норм-вью на сегмент
        views = {}
        for seg in doc.segments:
            if not seg.text:
                continue
            norm, omap = detection_view(seg)
            views[seg.id] = (norm, norm.lower(), omap)

        entities: list[Entity] = []
        emitted_spans: dict[str, list] = {}   # seg_id -> [(ns,ne)] уже помеченные

        # --- PASS 1: якоря ---
        detector_by_key: dict[tuple, AnchorDetector] = {}
        for seg in doc.segments:
            if seg.id not in views:
                continue
            norm, low, omap = views[seg.id]
            inn_ogrn = inn_by_seg.get(seg.id, [])
            for det in self.detectors:
                for a in det.detect(seg, norm, low, omap, inn_ogrn):
                    registry.add(a.core_key, det.entity_type, a.canonical,
                                 a.evidence, a.confidence)
                    detector_by_key[a.core_key] = det
                    self._emit(entities, emitted_spans, seg, norm, omap,
                               a.span_start, a.span_end, det.entity_type)

        # --- PASS 2: падежные вхождения по реестру + guard ---
        for seg in doc.segments:
            if seg.id not in views:
                continue
            norm, low, omap = views[seg.id]
            tokens = list(_WORD_RE.finditer(norm))
            if not tokens:
                continue
            lemsets = [_lemma_set(t.group(0)) for t in tokens]
            for rec in registry.records():
                det = detector_by_key.get(rec.key)
                if det is None:
                    continue
                self._match_key(entities, emitted_spans, seg, norm, low, omap,
                                tokens, lemsets, rec, det)

        return entities

    def _match_key(self, entities, emitted_spans, seg, norm, low, omap,
                   tokens, lemsets, rec, det):
        klen = len(rec.key)
        n = len(tokens)
        i = 0
        while i + klen <= n:
            ok = all(rec.key[j] in lemsets[i + j] for j in range(klen))
            if not ok:
                i += 1
                continue
            s = tokens[i].start()
            e = tokens[i + klen - 1].end()
            if self._covered(emitted_spans, seg.id, s, e):
                i += klen
                continue
            if not det.guard(norm, low, s, e):
                i += klen
                continue
            self._emit(entities, emitted_spans, seg, norm, omap, s, e,
                       rec.entity_type)
            i += klen

    @staticmethod
    def _covered(emitted_spans, seg_id, s, e):
        for (a, b) in emitted_spans.get(seg_id, ()):
            if max(a, s) < min(b, e):
                return True
        return False

    @staticmethod
    def _emit(entities, emitted_spans, seg, norm, omap, ns, ne, entity_type):
        # обрезаем крайние пробелы/кавычки-скобки норм-спана
        while ns < ne and norm[ns] in " \t":
            ns += 1
        while ne > ns and norm[ne - 1] in " \t":
            ne -= 1
        if ns >= ne:
            return
        src_start, src_end = norm_to_src(omap, ns, ne)
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
        emitted_spans.setdefault(seg.id, []).append((ns, ne))


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
    """Возвращает список ORG-сущностей (якоря + падежные вхождения). Тип — один
    (ORG), движок типо-параметричен для будущих PER/ADDRESS."""
    engine = AnchorEngine([OrgAnchorDetector()])
    return engine.run(doc, regex_entities=regex_entities)
