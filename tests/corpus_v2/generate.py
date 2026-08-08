# -*- coding: utf-8 -*-
"""
generate.py — ГЕНЕРАТОР КОРПУСА V2. Отдельная копия, а не правка оригинала.

Оригинал (tests/corpus/generate.py) заморожен вместе со старым корпусом и
входит в его MANIFEST.sha256. Дублирование ~2000 строк — осознанная цена
за то, что цифры старого корпуса остаются сравнимыми.

ЧЕМ ОТЛИЧАЕТСЯ ОТ ОРИГИНАЛА
1. Новые виды данных: MONEY, PERCENT, TERM, TRANCHE (см. values.py). В
   оригинале суммы были НЕГАТИВАМИ («сумма — не ПДн»), а проценты и сроки
   вообще прятались внутри готовых литералов клауз и шума — координат у них
   не было. Здесь каждый вставленный кусок данных сам записывает вид, начало
   и конец: клаузы и шум перестали быть строками и стали списками частей
   (values.T — связка, values.E — размеченная величина).
2. Две РАЗДЕЛЬНЫЕ группы структуры (`structure_group`):
     "simple"  — как в старом корпусе. На ней меряются новые виды данных,
                 чтобы результат не смешивался с потерями чтения;
     "complex" — отслеживаемые правки, поля Word, умные теги, элементы формы,
                 колонтитулы, документы на 2000+ абзацев. Нужна не для метрик
                 новых видов, а чтобы потери чтения были измеримы и видны числом.
   Группы не смешиваются: пометка стоит у каждого документа в разметке.
3. Мутатор (mutate.py) не копировался: состязательные приёмы над ПДн уже
   измерены на старом корпусе, а V2 существует ради новых видов данных.

КРАСНАЯ ЛИНИЯ. Цифры, полученные на порождённом корпусе, — это ВЕРХНЯЯ
ГРАНИЦА, а не измерение. Корпус проверяет детектор против замысла генератора,
а не против реальности: конструкция, которую генератор не предусмотрел, не
появится ни в тексте, ни в эталоне, и её отсутствие будет невидимым.
Настоящая проверка — только ручная разметка реальных договоров владельцем.

Разметка ставится НА ЧАНК В МОМЕНТ ГЕНЕРАЦИИ (метод D.E). Координаты никогда
не ищутся в готовом тексте — они вычисляются сериализатором из модели.
"""
import os
import random
import re
import sys

import data as DT
import values as V
from corpus_lib import (NBSP, NNBSP, ZWSP, ZWJ, SHY, WJ, CYR2LAT, DIGIT2CYR,
                        chunk, ent, neg as cl_neg, para, table, cell, textbox,
                        render, update_gold, serialize, gold_entry)
import typedef_lib

# ЭТАП TYPE-FACTORY-2: фабричные типы (typedefs с полной generator-секцией)
# вплетаются в документы отдельным блоком. Загружается один раз на импорт —
# состав детерминирован содержимым каталога typedefs/.
FACTORY_TYPES = typedef_lib.load_factory_types()

ROOT = os.path.dirname(os.path.abspath(__file__))

CONTRACTS = [
    ("supply", "поставка", 16), ("services", "оказание услуг", 14),
    ("lease", "аренда", 12), ("works", "подряд", 12), ("loan", "займ", 10),
    ("cession", "цессия", 8), ("agency", "агентский", 10),
    ("labor", "трудовой", 12), ("sale", "купля-продажа", 14),
]

TITLES = {
    "supply": "ДОГОВОР ПОСТАВКИ", "services": "ДОГОВОР ВОЗМЕЗДНОГО ОКАЗАНИЯ УСЛУГ",
    "lease": "ДОГОВОР АРЕНДЫ НЕЖИЛОГО ПОМЕЩЕНИЯ", "works": "ДОГОВОР ПОДРЯДА",
    "loan": "ДОГОВОР ЗАЙМА", "cession": "ДОГОВОР УСТУПКИ ПРАВА ТРЕБОВАНИЯ (ЦЕССИИ)",
    "agency": "АГЕНТСКИЙ ДОГОВОР", "labor": "ТРУДОВОЙ ДОГОВОР",
    "sale": "ДОГОВОР КУПЛИ-ПРОДАЖИ",
}
ROLES = {
    "supply": ("Поставщик", "Покупатель"), "services": ("Исполнитель", "Заказчик"),
    "lease": ("Арендодатель", "Арендатор"), "works": ("Подрядчик", "Заказчик"),
    "loan": ("Займодавец", "Заёмщик"), "cession": ("Цедент", "Цессионарий"),
    "agency": ("Агент", "Принципал"), "labor": ("Работодатель", "Работник"),
    "sale": ("Продавец", "Покупатель"),
}

DOCX_POOL = ["hdr_pii", "ftr_pii", "footnote_pii", "textbox_pii", "nested_table",
             "bold_split", "split_cell", "caps_style_lower", "bare_cell"]
UGLY_POOL = ["addr_no_marker", "addr_index_first", "addr_city_middle", "addr_nonres",
             "addr_pobox", "oblique_names", "initials_tight", "org_noquote",
             "org_fio_inside", "composite_ip", "exotic_name", "word_surname",
             "phone_zoo", "passport", "snils", "birthdate", "req_spaced",
             "org_name_is_person", "appendix_pii", "invalid_checksum", "kpp_combo"]
ADV_POOL = ["homoglyph", "invisible", "case_lower", "case_upper", "case_mixed",
            "digit_spaces", "linebreak_entity", "dense_line", "addr_glued",
            "same_name_5_cases", "email_homoglyph", "zw_in_name"]

# ЭТАП A1 — формы записи «паспорт»/«серия» на реальном договоре (DOG-PASSPORT-GAP):
# (разделитель серия->номер, доп. текст между якорем и серией). Доп. текст удлиняет
# зазор якорь->серия ближе к фактическому максимуму (см. entity_types.yaml,
# зазор поднят [^\n\d]{0,20} -> {0,40}), не превышая его. Флаг "passport" (уже был
# в UGLY_POOL, но не читался никем) включает выбор одного из этих стилей на документ.
PASSPORT_STYLES = [
    ("space", ""),
    ("comma_no", ""),
    ("space_no", ""),
    ("nbsp", ""),
    ("space", "гражданина Российской Федерации "),
    ("comma_no", "гражданина РФ, выданного "),
    ("linebreak", ""),
]

# ЭТАП A2 — та же болезнь у трёх других типов (A1-RIGID-GAP-SURVEY): короткий/
# средний/длинный зазор якорь->значение, разный регистр якоря. Разделитель
# ВНУТРИ значения (между триплетами СНИЛС, между частями кода подразделения,
# между днём/месяцем/годом) НЕ варьируем шире дефиса/пробела — попытка снять
# ограничение (`\s?`) дала регресс BIRTHDATE на корпусе v1 (`\s` матчит и
# СТРУКТУРНЫЙ перенос сегмента/ячейки, не только перенос внутри прозы), см.
# entity_types.yaml, находка A2-NEWLINE-CROSS. Флаги "snils"/"birthdate" (были
# в UGLY_POOL с этапа CORPUS-V2, не читались никем) включают выбор стиля.
SNILS_STYLES = [
    ("-", "СНИЛС "),
    (" ", "СНИЛС № "),
    ("-", "снилс: "),
    ("-", "Страховое свидетельство государственного пенсионного страхования № "),
]
BIRTHDATE_STYLES = [
    (".", "Дата рождения: "),
    ("/", "Дата рождения: "),
    (".", "ДАТА РОЖДЕНИЯ: "),
    (".", "Дата рождения (согласно паспортным данным): "),
]
DEPTCODE_STYLES = [
    ("-", "код подразделения "),
    (" ", "код подразделения "),
    ("-", "Код подразделения "),
    ("-", "код подразделения, выдавшего паспорт: "),
    ("-", "код подр. "),
]


# --------------------------------------------------------------------- Doc
class D:
    def __init__(self, doc_id, fmt, ctype, seed, flags, group="simple",
                 form_start=0, struct_tricks=()):
        self.doc_id, self.format, self.ctype = doc_id, fmt, ctype
        self.rnd = random.Random(seed)
        self.flags = flags
        self._n = 0
        self.header, self.footer, self.body = [], [], []
        self.parties = ""
        self.group = group
        self.struct_tricks = list(struct_tricks)
        # Курсор по формам записи: у каждого документа своё СМЕЩЕНИЕ, поэтому
        # за корпус в целом перебираются все формы каждого вида данных, а не
        # только первые. Без этого «разнообразие» свелось бы к тому, какие
        # формы попались первым документам.
        self._form_start = form_start
        self._form_cursor = {}

    def nid(self):
        self._n += 1
        return "%s-e%d" % (self.doc_id, self._n)

    def t(self, s):
        return chunk(s)

    def E(self, s, type_, cat="canonical", trick=None, note=None, checksum=None,
          eid=None, bs=False, form=None, wrap=None, instr=None, axes=None):
        return chunk(s, ent=ent(type_, cat, eid or self.nid(), trick, note,
                                checksum, form, axes),
                     bold_split=bs, wrap=wrap, instr=instr)

    def N(self, s, why, type_=None, form=None, axes=None, trick=None, nid=None,
          bs=False, lookalike=False, kind=None):
        # `bs` здесь обязателен наравне с сущностями: номер договора — тоже
        # вхождение вида данных, и разрыв форматированием ему полагается такой
        # же. Без этого приём проставлялся бы в разметке, но до .docx не
        # доходил — пометка есть, ловушки нет.
        n = cl_neg(why, type_, form, axes, lookalike=lookalike, kind=kind)
        if trick:
            n["trick"] = trick
        if nid:
            # Общий id — негатив, разорванный границей ячейки, остаётся ОДНИМ
            # вхождением (см. corpus_lib._Out.emit).
            n["id"] = nid
        return chunk(s, neg=n, bold_split=bs)

    def I(self, s, why):
        return chunk(s, ignore={"why": why})

    # ---------------------------------------------------------- новые виды
    def P(self, parts, wrap=None):
        """Части values.T / values.E / values.NEG -> чанки.

        ЭТО И ЕСТЬ «каждый вставленный кусок данных сам записывает: что это за
        вид данных, где начинается, где кончается». Координаты считает
        сериализатор из модели — здесь их никто не ищет в тексте.
        """
        out = []
        for p in parts:
            if p[0] == "t":
                out.append(chunk(p[1], wrap=wrap))
            elif p[0] == "n":
                _, s, type_, why, form, axes, kind = p
                out.append(chunk(s, neg=cl_neg(why, type_, form, axes, kind=kind),
                                 wrap=wrap))
            else:
                _, s, type_, form, note, cat, axes = p
                out.append(self.E(s, type_, cat, note=note, form=form, wrap=wrap,
                                  axes=axes))
        return out

    def val(self, kind):
        """Очередная КОМБИНАЦИЯ ЗНАЧЕНИЙ ОСЕЙ вида данных `kind`.

        Раньше здесь брали следующую функцию из списка форм. Теперь список
        форм не существует: реестр values.py раздаёт значения по каждой оси
        независимым счётчиком, а форма — их комбинация. Счётчик `k` свой у
        каждого вида данных внутри документа, смещение `_form_start` — своё у
        каждого документа: иначе все документы начинали бы обход осей с одной
        и той же точки и корпус получил бы перекос.
        """
        k = self._form_cursor.get(kind, 0)
        self._form_cursor[kind] = k + 1
        return V.BUILDERS[kind](k, self._form_start, self.rnd)

    def adv_val(self, kind):
        """Величина под состязательный приём — гарантированно с цифрами."""
        k = self._form_cursor.get(kind, 0)
        self._form_cursor[kind] = k + 1
        return V.adversarial_source(kind, k, self._form_start, self.rnd)

    def model(self):
        m = {"doc_id": self.doc_id, "format": self.format, "source": "base_v2",
             "structure_group": self.group,
             "structure_tricks": sorted(self.struct_tricks),
             "contract_type": self.ctype, "body": self.body,
             "parties": self.parties, "features": sorted(self.flags)}
        if self.header:
            m["header"] = self.header
        if self.footer:
            m["footer"] = self.footer
        return m


def has(d, f):
    return f in d.flags


# --------------------------------------------------------------------- адреса
def make_address(rnd, style):
    city_s, city_g, idx = rnd.choice(DT.CITIES)
    st = rnd.choice(DT.STREETS)
    stt = rnd.choice(DT.STREET_TYPE)
    h = rnd.randint(1, 90)
    if style == "canonical":
        return "%s, %s, %s %s, д. %d, кв. %d" % (idx, city_g, stt, st, h, rnd.randint(1, 200))
    if style == "no_marker":
        return "%s, %s %d" % (city_s, st, h)
    if style == "no_marker2":
        return "%s %s %d/%d кв %d" % (city_s, st, h, rnd.randint(1, 4), rnd.randint(1, 90))
    if style == "index_first":
        return "%s, %s, г. %s, %s %s, вл. %d, стр. %d, пом. VII" % (
            idx, rnd.choice(DT.REGIONS), city_s, stt, st, h, rnd.randint(1, 5))
    if style == "city_middle":
        return "%s %s, д. %d, %s, %s" % (stt, st, h, rnd.choice(DT.SETTLEMENTS),
                                         rnd.choice(DT.REGIONS))
    if style == "nonres":
        return "%s, %s, г. %s, %s %s, д. %d, корп. %d, литера А, пом. %d, оф. %d" % (
            idx, rnd.choice(DT.REGIONS), city_s, stt, st, h, rnd.randint(1, 3),
            rnd.randint(1, 30), rnd.randint(100, 500))
    if style == "pobox":
        return "а/я %d, г. %s, %s" % (rnd.randint(1, 99), city_s, idx)
    if style == "km":
        return "%s, %s р-н, %d км автодороги М-%d «Дон», стр. %d" % (
            rnd.choice(DT.REGIONS), rnd.choice(["Ленинский", "Наро-Фоминский", "Дмитровский"]),
            rnd.randint(12, 90), rnd.randint(1, 9), rnd.randint(1, 6))
    if style == "snt":
        return "%s, тер. СНТ «Родник», уч. %d" % (rnd.choice(DT.REGIONS), rnd.randint(1, 300))
    raise ValueError(style)


def addr_style(d, canonical_ok=True):
    pool = []
    if has(d, "addr_no_marker"):
        pool += ["no_marker", "no_marker2"]
    if has(d, "addr_index_first"):
        pool += ["index_first"]
    if has(d, "addr_city_middle"):
        pool += ["city_middle"]
    if has(d, "addr_nonres"):
        pool += ["nonres", "km", "snt"]
    if has(d, "addr_pobox"):
        pool += ["pobox"]
    if not pool:
        return "canonical", "canonical"
    s = d.rnd.choice(pool)
    return s, "ugly"


def ADDR(d, note=None):
    style, cat = addr_style(d)
    return d.E(make_address(d.rnd, style), "ADDRESS", cat,
               note=note or ("стиль адреса: %s" % style))


# --------------------------------------------------------------------- телефоны
def phone_str(rnd, style):
    a = rnd.choice(["495", "499", "812", "843", "383", "846"])
    b, c, e = rnd.randint(100, 999), rnd.randint(10, 99), rnd.randint(10, 99)
    if style == "canonical":
        return "+7 (%s) %03d-%02d-%02d" % (a, b, c, e)
    if style == "spaces":
        return "+7 %s %03d %02d %02d" % (a, b, c, e)
    if style == "tight8":
        return "8(%s)%03d%02d%02d" % (a, b, c, e)
    if style == "dashes8":
        return "8-%s-%03d-%02d-%02d" % (a, b, c, e)
    if style == "plain":
        return "+7%s%03d%02d%02d" % (a, b, c, e)
    if style == "faxlike":
        return "%s %03d%02d%02d" % (a, b, c, e)
    if style == "ext":
        return "+7 (%s) %03d-%02d-%02d доб. %d" % (a, b, c, e, rnd.randint(100, 999))
    raise ValueError(style)


def PHONE(d, force=None):
    if force:
        st = force
    elif has(d, "phone_zoo"):
        st = d.rnd.choice(["spaces", "tight8", "dashes8", "plain", "faxlike", "ext"])
    else:
        st = "canonical"
    cat = "canonical" if st == "canonical" else "ugly"
    return d.E(phone_str(d.rnd, st), "PHONE", cat, note="формат: %s" % st)


def email_for(rnd, person, org=None):
    tr = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
          "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
          "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
          "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
          "ю": "yu", "я": "ya", "-": ".", " ": "."}
    sur = person.get("sur") or person["nom"].split()[0]
    lat = "".join(tr.get(ch.lower(), ch.lower()) for ch in sur if ch.isalpha() or ch in "- ")
    dom = rnd.choice(DT.EMAIL_DOM)
    return "%s@%s" % (lat or "info", dom)


# --------------------------------------------------------------------- стороны
def spaced(num, group=3):
    out, s = [], num
    while s:
        out.insert(0, s[-group:])
        s = s[:-group]
    return " ".join(out)


def mk_org_name(d):
    rnd = d.rnd
    if has(d, "org_fio_inside"):
        return rnd.choice(DT.ORG_WITH_FIO), "ugly", "ФИО внутри названия организации"
    if has(d, "org_name_is_person"):
        return "ООО «%s»" % rnd.choice(DT.ORG_FIO_NAME), "ugly", "название ЮЛ = фамилия"
    if has(d, "org_noquote"):
        v = rnd.choice(["ООО %s" % rnd.choice(DT.ORG_CORE),
                        'ООО "%s"' % rnd.choice(DT.ORG_CORE),
                        rnd.choice(DT.ORG_NOQUOTE),
                        "ООО «%s»" % rnd.choice(DT.ORG_CORE)])
        return v, "ugly", "организация без кавычек / нестандартные кавычки"
    form = rnd.choice(["ООО", "ООО", "АО", "ПАО"])
    return "%s «%s»" % (form, rnd.choice(DT.ORG_CORE)), "canonical", None


def mk_le(d):
    rnd = d.rnd
    bad = has(d, "invalid_checksum")
    name, cat, note = mk_org_name(d)
    bank, bik, corr = rnd.choice(DT.BANKS)
    person = DT.make_person(rnd, exotic=has(d, "exotic_name") and rnd.random() < 0.5,
                            word_surname=has(d, "word_surname") and rnd.random() < 0.4)
    return dict(kind="LE", name=name, name_cat=cat, name_note=note,
                inn=DT.inn10(rnd, not bad), kpp=DT.kpp(rnd), ogrn=DT.ogrn13(rnd, not bad),
                bank=bank, bik=bik, corr=corr, acc=DT.account(rnd, bik, valid=not bad),
                person=person,
                position=rnd.choice(["генерального директора", "директора",
                                     "исполнительного директора", "управляющего"]),
                position_nom=rnd.choice(["Генеральный директор", "Директор", "Управляющий"]),
                basis=rnd.choice(["Устава", "Устава", "доверенности № 12 от 09.01.2024",
                                  "приказа № 5-к от 10.01.2024"]),
                bad=bad)


def mk_ip(d):
    rnd = d.rnd
    bad = has(d, "invalid_checksum")
    person = DT.make_person(rnd, exotic=has(d, "exotic_name") and rnd.random() < 0.5,
                            word_surname=has(d, "word_surname") and rnd.random() < 0.4)
    bank, bik, corr = rnd.choice(DT.BANKS)
    return dict(kind="IP", person=person, inn=DT.inn12(rnd, not bad),
                ogrnip=DT.ogrnip15(rnd, not bad), bank=bank, bik=bik, corr=corr,
                acc=DT.account(rnd, bik, prefix="40802810", valid=not bad),
                basis="свидетельства о государственной регистрации", bad=bad)


def mk_np(d):
    rnd = d.rnd
    bad = has(d, "invalid_checksum")
    person = DT.make_person(rnd, exotic=has(d, "exotic_name") and rnd.random() < 0.5,
                            word_surname=has(d, "word_surname") and rnd.random() < 0.4)
    bank, bik, corr = rnd.choice(DT.BANKS)
    pass_series, pass_number = DT.passport_parts(rnd)
    pass_style = rnd.choice(PASSPORT_STYLES) if has(d, "passport") else ("space", "")
    snils_style = rnd.choice(SNILS_STYLES) if has(d, "snils") else ("-", "СНИЛС ")
    birth_style = rnd.choice(BIRTHDATE_STYLES) if has(d, "birthdate") else (".", "Дата рождения: ")
    dept_style = rnd.choice(DEPTCODE_STYLES) if has(d, "passport") else ("-", "код подразделения ")
    return dict(kind="NP", person=person, inn=DT.inn12(rnd, not bad),
                snils=DT.snils_value(DT.snils_parts(rnd, not bad), snils_style[0]),
                snils_intro=snils_style[1],
                passport=DT.passport_value(pass_series, pass_number, pass_style[0]),
                passport_intro=pass_style[1],
                passport_cat="canonical" if pass_style == ("space", "") else "ugly",
                pass_dept=DT.deptcode_value(DT.deptcode_parts(rnd), dept_style[0]),
                pass_dept_intro=dept_style[1],
                pass_org=rnd.choice(['ОВД «Тропарёво» г. Москвы', 'ГУ МВД России по г. Москве',
                                     'ОУФМС России по Тверской области в Заволжском р-не',
                                     'ТП № 2 ОУФМС России по Московской обл.']),
                pass_date="%02d.%02d.%d" % (rnd.randint(1, 28), rnd.randint(1, 12),
                                            rnd.randint(2002, 2020)),
                bank=bank, bik=bik, corr=corr,
                acc=DT.account(rnd, bik, prefix="40817810", valid=not bad),
                birth=DT.birthdate_value(DT.birthdate_parts(rnd), birth_style[0]),
                birth_intro=birth_style[1],
                bad=bad)


def party_title(p):
    if p["kind"] == "LE":
        return p["name"]
    if p["kind"] == "IP":
        return "ИП " + p["person"]["short"]
    return p["person"]["nom"]


# --------------------------------------------------------------------- преамбула
def preamble(d, p1, p2, r1, r2):
    rnd = d.rnd
    ch = []
    for p, role in ((p1, r1), (p2, r2)):
        if p["kind"] == "LE":
            ch += [d.E(p["name"], "ORG", p["name_cat"], note=p["name_note"]),
                   d.t(", именуемое в дальнейшем «%s», в лице %s " % (role, p["position"]))]
            nm = p["person"]["gen"] if not has(d, "initials_tight") else p["person"]["short_tight"]
            cat = "ugly" if (has(d, "oblique_names") or has(d, "initials_tight")) else "canonical"
            note = "ФИО в родительном падеже" if not has(d, "initials_tight") else "инициалы слитно И.И.Иванов"
            ch += [d.E(nm, "PER", cat, note=note),
                   d.t(", действующего на основании "), d.t(p["basis"])]
        elif p["kind"] == "IP":
            pre = rnd.choice(["Индивидуальный предприниматель ", "ИП "])
            form = p["person"]["short"] if rnd.random() < 0.5 else p["person"]["nom"]
            ch += [d.t(pre),
                   d.E(form, "PER", "ugly", note="составная форма: %s+ФИО" % pre.strip()),
                   d.t(", ИНН "),
                   d.E(p["inn"], "INN", "canonical" if not p["bad"] else "canonical",
                       checksum="invalid" if p["bad"] else "valid",
                       note="ИНН физлица/ИП, 12 знаков"),
                   d.t(", именуем%s в дальнейшем «%s», действующ%s на основании %s"
                       % ("ая" if p["person"]["gender"] == "f" else "ый", role,
                          "ая" if p["person"]["gender"] == "f" else "ий", p["basis"]))]
        else:
            nm = p["person"]["nom"]
            ch += [d.E(nm, "PER", "canonical"),
                   d.t(", "),
                   d.t("паспорт " + p["passport_intro"]),
                   d.E(p["passport"], "PASSPORT", "ugly",
                       note="серия+номер, стиль записи из PASSPORT_STYLES"),
                   d.t(", выдан "), d.t(p["pass_org"]), d.t(" "),
                   d.I(p["pass_date"], "дата выдачи паспорта — серая зона (не ПДн-сущность в нашей схеме)"),
                   d.t(", " + p["pass_dept_intro"]),
                   d.E(p["pass_dept"], "PASSPORT", "ugly",
                       note="код подразделения, стиль записи из DEPTCODE_STYLES"),
                   d.t(", именуем%s в дальнейшем «%s»"
                       % ("ая" if p["person"]["gender"] == "f" else "ый", role))]
        if p is p1:
            ch += [d.t(", с одной стороны, и ")]
    # Номер договора — типизированный негатив с собственной осью формата
    # (задача 1 CORPUS-V2-B): форматы взяты из реестра контрактов, и детектор
    # обязан не срабатывать ни на одном из них.
    ch += [d.t(", с другой стороны, совместно именуемые «Стороны», заключили "
               "настоящий Договор № ")] \
        + d.P(d.val("DOCNUM")) \
        + [d.t(" (далее — Договор) о нижеследующем:")]
    return para(ch)


# --------------------------------------------------------------------- реквизиты
# Функция money() оригинала удалена намеренно: она порождала сумму ОДНИМ
# способом («123 456,00 руб.») мимо реестра форм, и такая сумма попадала в
# текст без идентификатора формы. В V2 все суммы идут только через
# values.MONEY_FORMS — иначе тест разнообразия считал бы формы, которых он не
# видит, а корпус учил бы детектор одному способу записи.
def req_lines(d, p, role, mode="normal"):
    """Список абзацев блока реквизитов стороны (для .txt и для ячейки .docx)."""
    rnd = d.rnd
    L = []
    L.append(para([d.t(role + ":")], style="bold"))
    if p["kind"] == "LE":
        L.append(para([d.E(p["name"], "ORG", p["name_cat"], note=p["name_note"])]))
        L.append(para([d.t("Юридический адрес: "), ADDR(d)]))
        if rnd.random() < 0.5:
            L.append(para([d.t("Почтовый адрес: "), ADDR(d)]))
        inn = spaced(p["inn"]) if has(d, "req_spaced") else p["inn"]
        ogrn = spaced(p["ogrn"], 3) if has(d, "req_spaced") else p["ogrn"]
        acc = spaced(p["acc"], 4) if has(d, "req_spaced") else p["acc"]
        cat = "ugly" if has(d, "req_spaced") else "canonical"
        cs = "invalid" if p["bad"] else "valid"
        # ЭТАП A4 — форма «ИНН/КПП <10 цифр>/<9 цифр>»: между словом-якорем «КПП»
        # и КПП-числом стоит ИНН-число, и обычное окно якоря (см.
        # entity_types.yaml, _has_anchor) до него не достаёт — риск, названный
        # владельцем прямо (см. DOG-KPP-NOANCHOR). Отдельная запись pattern в
        # entity_types.yaml матчит эту связку целиком; здесь — единственное
        # вхождение формы в корпус, чтобы риск был измерим, а не гипотетичен.
        if has(d, "kpp_combo"):
            L.append(para([d.t("ИНН/КПП "), d.E(inn, "INN", cat, checksum=cs,
                                                 note="пробелы внутри номера" if cat == "ugly" else None),
                           d.t("/"), d.E(p["kpp"], "KPP", "canonical",
                                         note="связка ИНН/КПП, якорь отрезан числом ИНН")]))
        else:
            L.append(para([d.t("ИНН "), d.E(inn, "INN", cat, checksum=cs,
                                            note="пробелы внутри номера" if cat == "ugly" else None),
                           d.t(" КПП "), d.E(p["kpp"], "KPP", "canonical")]))
        L.append(para([d.t("ОГРН "), d.E(ogrn, "OGRN", cat, checksum=cs,
                                         note="пробелы внутри номера" if cat == "ugly" else None)]))
        L.append(para([d.t("р/с "), d.E(acc, "ACCOUNT", cat, checksum=cs,
                                        note="пробелы внутри номера" if cat == "ugly" else None),
                       d.t(" в " + p["bank"])]))
        L.append(para([d.t("к/с "), d.E(p["corr"], "ACCOUNT", "canonical",
                                        note="корреспондентский счёт банка"),
                       d.t(" БИК "), d.E(p["bik"], "BIK", "canonical")]))
        L.append(para([d.t("Тел.: "), PHONE(d)]))
        L.append(para([d.t("E-mail: "), d.E(email_for(rnd, p["person"]), "EMAIL", "canonical")]))
        L.append(para([]))
        L.append(para([d.t(p["position_nom"] + " ______________ "),
                       d.E(p["person"]["initials"], "PER", "ugly",
                           note="инициалы перед фамилией")]))
    elif p["kind"] == "IP":
        L.append(para([d.t("Индивидуальный предприниматель "),
                       d.E(p["person"]["nom"], "PER", "canonical")]))
        L.append(para([d.t("Адрес регистрации: "), ADDR(d)]))
        L.append(para([d.t("ИНН "), d.E(p["inn"], "INN", "canonical",
                                        checksum="invalid" if p["bad"] else "valid",
                                        note="12-значный ИНН"),
                       d.t(" ОГРНИП "), d.E(p["ogrnip"], "OGRN", "ugly",
                                            checksum="invalid" if p["bad"] else "valid",
                                            note="ОГРНИП, 15 знаков")]))
        L.append(para([d.t("р/с "), d.E(p["acc"], "ACCOUNT", "canonical",
                                        checksum="invalid" if p["bad"] else "valid"),
                       d.t(" в " + p["bank"]), d.t(", БИК "), d.E(p["bik"], "BIK", "canonical")]))
        L.append(para([d.t("Тел.: "), PHONE(d)]))
        L.append(para([d.t("E-mail: "), d.E(email_for(rnd, p["person"]), "EMAIL", "canonical")]))
        L.append(para([]))
        L.append(para([d.t("ИП ______________ "),
                       d.E(p["person"]["short"], "PER", "ugly", note="фамилия + инициалы")]))
    else:
        L.append(para([d.E(p["person"]["nom"], "PER", "canonical")]))
        birth_cat = "canonical" if p["birth_intro"] == "Дата рождения: " else "ugly"
        L.append(para([d.t(p["birth_intro"]),
                       d.E(p["birth"], "BIRTHDATE", birth_cat,
                           note="стиль записи из BIRTHDATE_STYLES")]))
        L.append(para([d.t("Адрес регистрации: "), ADDR(d)]))
        L.append(para([d.t("Паспорт " + p["passport_intro"]),
                       d.E(p["passport"], "PASSPORT", p["passport_cat"],
                           note="серия+номер, стиль записи из PASSPORT_STYLES"),
                       d.t(", выдан " + p["pass_org"] + " " + p["pass_date"]),
                       d.t(", " + p["pass_dept_intro"]),
                       d.E(p["pass_dept"], "PASSPORT", "ugly",
                           note="код подразделения, стиль записи из DEPTCODE_STYLES")]))
        snils_cat = "canonical" if p["snils_intro"] == "СНИЛС " else "ugly"
        L.append(para([d.t(p["snils_intro"]),
                       d.E(p["snils"], "SNILS", snils_cat,
                           note="стиль записи из SNILS_STYLES",
                           checksum="invalid" if p["bad"] else "valid"),
                       d.t(" ИНН "), d.E(p["inn"], "INN", "ugly",
                                         checksum="invalid" if p["bad"] else "valid",
                                         note="ИНН физлица (12 знаков)")]))
        L.append(para([d.t("Тел.: "), PHONE(d)]))
        L.append(para([d.t("E-mail: "), d.E(email_for(rnd, p["person"]), "EMAIL", "canonical")]))
        L.append(para([]))
        L.append(para([d.t("______________ "),
                       d.E(p["person"]["short"], "PER", "ugly", note="фамилия + инициалы")]))
    return L


def dense_line(d, p):
    """Плотная строка реквизитов без пунктуации."""
    rnd = d.rnd
    parts = []
    if p["kind"] == "LE":
        parts += [d.E(p["name"].replace("«", "").replace("»", ""), "ORG", "adversarial",
                      trick="dense_line", note="без кавычек, в плотной строке"),
                  d.t(" ИНН "), d.E(p["inn"], "INN", "adversarial", trick="dense_line"),
                  d.t(" КПП "), d.E(p["kpp"], "KPP", "adversarial", trick="dense_line"),
                  d.t(" "), d.E(make_address(rnd, "no_marker").replace(",", " ").replace("г. ", "г "),
                                "ADDRESS", "adversarial", trick="dense_line",
                                note="адрес без пунктуации и маркеров"),
                  d.t(" тел "), d.E(phone_str(rnd, "plain").replace("+7", "8"), "PHONE",
                                    "adversarial", trick="dense_line")]
    else:
        parts += [d.t("ИП "), d.E(p["person"]["short_tight"], "PER", "adversarial",
                                  trick="dense_line", note="инициалы слитно"),
                  d.t(" ИНН "), d.E(p["inn"], "INN", "adversarial", trick="dense_line"),
                  d.t(" "), d.E(make_address(rnd, "no_marker"), "ADDRESS", "adversarial",
                                trick="dense_line"),
                  d.t(" тел "), d.E(phone_str(rnd, "plain"), "PHONE", "adversarial",
                                    trick="dense_line")]
    return para(parts)


# --------------------------------------------------------------------- состязательные приёмы
def homoglyph_text(s, rnd, kind="mixed"):
    out = []
    changed = 0
    for c in s:
        r = rnd.random()
        if c.isdigit() and kind in ("digits", "mixed") and c in DIGIT2CYR and r < 0.25 and changed < 3:
            out.append(DIGIT2CYR[c]); changed += 1
        elif c in CYR2LAT and kind in ("letters", "mixed") and r < 0.30 and changed < 4:
            out.append(CYR2LAT[c]); changed += 1
        else:
            out.append(c)
    if changed == 0:  # гарантируем хотя бы одну подмену
        for i, c in enumerate(out):
            if c in CYR2LAT:
                out[i] = CYR2LAT[c]; break
            if c in DIGIT2CYR:
                out[i] = DIGIT2CYR[c]; break
    return "".join(out)


def invisible_text(s, rnd):
    chars = [NBSP, NNBSP, ZWSP, ZWJ, SHY, WJ]
    pos = sorted(rnd.sample(range(1, max(2, len(s) - 1)), k=min(2, max(1, len(s) // 5))))
    out, prev = [], 0
    for p in pos:
        out.append(s[prev:p]); out.append(rnd.choice(chars)); prev = p
    out.append(s[prev:])
    return "".join(out)


def adversarial_block(d, p1, p2):
    """Отдельный раздел с состязательными формами (только если флаги заданы)."""
    rnd = d.rnd
    L = []
    if not any(has(d, f) for f in ADV_POOL):
        return L
    L.append(para([]))
    L.append(para([d.t("10. ДОПОЛНИТЕЛЬНЫЕ СВЕДЕНИЯ О СТОРОНАХ")], style="bold"))

    if has(d, "homoglyph"):
        src = p1 if p1["kind"] == "LE" else p2
        inn = src.get("inn")
        L.append(para([d.t("Контрольный ИНН: "),
                       d.E(homoglyph_text(inn, rnd, "digits"), "INN", "adversarial",
                           trick="homoglyph_cyrillic_in_digits",
                           note="кириллические О/З/Ч на месте цифр 0/3/4")]))
        per = (src["person"]["nom"])
        L.append(para([d.t("Ответственное лицо: "),
                       d.E(homoglyph_text(per, rnd, "letters"), "PER", "adversarial",
                           trick="homoglyph_latin_in_cyrillic",
                           note="латинские A/E/O/C/P внутри русского ФИО")]))
        if "bik" in src:
            L.append(para([d.t("БИК банка: "),
                           d.E(homoglyph_text(src["bik"], rnd, "digits"), "BIK", "adversarial",
                               trick="homoglyph_cyrillic_in_digits")]))
    if has(d, "invisible"):
        src = p2
        L.append(para([d.t("Дополнительный телефон: "),
                       d.E(invisible_text(phone_str(rnd, "canonical"), rnd), "PHONE",
                           "adversarial", trick="invisible_chars",
                           note="NBSP/ZWSP/SHY внутри номера")]))
        L.append(para([d.t("Контактное лицо: "),
                       d.E(invisible_text(src["person"]["nom"], rnd), "PER", "adversarial",
                           trick="invisible_chars", note="zero-width внутри ФИО")]))
    if has(d, "zw_in_name"):
        src = p1
        nm = src["person"]["nom"]
        L.append(para([d.t("Уполномоченный представитель: "),
                       d.E(nm.replace(" ", ZWJ + " ", 1).replace("ов", "о" + SHY + "в", 1),
                           "PER", "adversarial", trick="zwj_and_soft_hyphen",
                           note="ZWJ на границе слова, мягкий перенос внутри фамилии")]))
    if has(d, "case_lower"):
        src = p2
        L.append(para([d.t("подписано "),
                       d.E(src["person"]["ins"].lower(), "PER", "adversarial",
                           trick="lowercase", note="полностью строчное ФИО в творительном падеже")]))
    if has(d, "case_upper"):
        src = p1
        L.append(para([d.t("ОТВЕТСТВЕННЫЙ: "),
                       d.E(src["person"]["nom"].upper(), "PER", "adversarial",
                           trick="uppercase", note="ALL CAPS ФИО")]))
    if has(d, "case_mixed"):
        src = p2
        nm = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(src["person"]["sur"] if src["person"].get("sur") else src["person"]["nom"].split()[0]))
        L.append(para([d.t("Согласовано: "),
                       d.E(nm + " " + src["person"]["nom"].split()[1], "PER", "adversarial",
                           trick="alternating_case", note="ПеТрОв-регистр")]))
    if has(d, "digit_spaces"):
        src = p1 if "acc" in p1 else p2
        L.append(para([d.t("Расчётный счёт (дублирование): "),
                       d.E(" ".join(src["acc"][i:i + 4] for i in range(0, 20, 4)),
                           "ACCOUNT", "adversarial", trick="digit_grouping",
                           note="счёт разбит на группы по 4")]))
        L.append(para([d.t("ИНН (дублирование): "),
                       d.E("-".join([src["inn"][:3], src["inn"][3:6], src["inn"][6:]]),
                           "INN", "adversarial", trick="digit_grouping_dashes",
                           note="ИНН через дефисы")]))
    if has(d, "linebreak_entity"):
        src = p2
        nm = src["person"]["nom"]
        k = nm.find(" ")
        L.append(para([d.t("Получатель уведомлений: "),
                       d.E(nm[:k] + "\n" + nm[k + 1:], "PER", "adversarial",
                           trick="linebreak_inside_entity",
                           note="ФИО разорвано переводом строки")]))
        acc = src["acc"]
        L.append(para([d.t("Счёт для возврата: "),
                       d.E(acc[:9] + "\n" + acc[9:], "ACCOUNT", "adversarial",
                           trick="linebreak_inside_entity",
                           note="номер счёта разорван переводом строки")]))
    if has(d, "addr_glued"):
        a = make_address(rnd, "canonical")
        inn = DT.inn10(rnd)
        L.append(para([d.t("Место исполнения: "),
                       d.E(a, "ADDRESS", "adversarial", trick="glued_to_next_entity",
                           note="адрес вплотную к ИНН без разделителя"),
                       d.E(inn, "INN", "adversarial", trick="glued_to_prev_entity",
                           note="ИНН приклеен к адресу")]))
    if has(d, "email_homoglyph"):
        e = email_for(rnd, p1["person"])
        e2 = e.replace("a", "а", 1) if "a" in e else e.replace("o", "о", 1)
        L.append(para([d.t("Резервный e-mail: "),
                       d.E(e2, "EMAIL", "adversarial", trick="homoglyph_in_email",
                           note="кириллическая буква внутри латинского домена/логина")]))
    if has(d, "dense_line"):
        L.append(dense_line(d, p1))
    if has(d, "same_name_5_cases"):
        pr = p2["person"]
        L.append(para([d.t("Претензии направляются "),
                       d.E(pr["dat"], "PER", "ugly", note="дательный"),
                       d.t("; ответ подписывается "),
                       d.E(pr["ins"], "PER", "ugly", note="творительный"),
                       d.t("; интересы "),
                       d.E(pr["gen"], "PER", "ugly", note="родительный"),
                       d.t(" представляет "),
                       d.E(pr["nom"], "PER", "canonical", note="именительный"),
                       d.t("; копия вручается "),
                       d.E(pr["short"], "PER", "ugly", note="фамилия+инициалы"),
                       d.t(" лично.")]))
    return L


# ============================================================================
#   СОСТЯЗАТЕЛЬНЫЕ ПРИЁМЫ НАД НОВЫМИ ВИДАМИ ДАННЫХ (задача 3 CORPUS-V2-B)
# ============================================================================
# До этого этапа приёмов у сумм, процентов, сроков и номеров НЕ БЫЛО ВОВСЕ:
# гомоглифы, невидимые символы и разрывы применялись только к ПДн старого
# набора (ФИО, ИНН, счета, адреса). Метрика новых видов измерялась на чистом
# тексте и потому была заведомо оптимистичной.
#
# ПОЧЕМУ РАЗРЫВУ ФОРМАТИРОВАНИЕМ ОТДАНО БОЛЬШЕ ВСЕГО ВХОЖДЕНИЙ. Это самый
# вероятный реальный случай, и он не «атака»: Word разрывает ран на границе
# любой правки — сменили начертание половине числа, вставили и удалили символ,
# прошлись проверкой орфографии. Число «10 000» лежит в документе двумя ранами
# сплошь и рядом, безо всякого умысла. Остальные приёмы реже, но каждый
# встречается.
#
# ГДЕ РАЗРЫВ. Разрыв обязан приходиться ВНУТРЬ ЧИСЛА, между двумя цифрами.
# Разрыв «10 000 | (Десять тысяч) рублей» проверял бы не то, что заявлено, —
# он не рвёт число. Поэтому позиция считается по самой длинной цепочке цифр.
def digit_cut(s):
    """Позиция разрыва между двумя цифрами самой длинной цепочки цифр."""
    best, cur, start = (0, 0), 0, 0
    for i, ch in enumerate(s + " "):
        if ch.isdigit():
            if not cur:
                start = i
            cur += 1
        else:
            if cur > best[1]:
                best = (start, cur)
            cur = 0
    start, ln = best
    if ln >= 2:
        return start + ln // 2
    raise ValueError("в %r нет цепочки хотя бы из двух цифр" % s)


def digit_spaces(s):
    """Пробелы внутри цифр: «10000» -> «10 0 00». Реальный случай ручного
    набора и вставки из таблицы."""
    i = digit_cut(s)
    return s[:i] + " " + s[i:i + 1] + " " + s[i + 1:]


def _adv_apply(d, part, trick, note):
    """Приём над ОДНОЙ величиной. Возвращает список чанков.

    Возвращается список, а не чанк, потому что граница ячейки и вправду делает
    из величины два куска; остальные приёмы дают один.
    """
    rnd = d.rnd
    if part[0] == "n":
        _, s, type_, why, form, axes, neg_kind = part
        kind, cat = "n", None
    else:
        _, s, type_, form, pnote, cat, axes = part
        kind = "e"

    bs = False
    if trick == "format_split":
        bs = digit_cut(s)
    elif trick == "homoglyph":
        s = homoglyph_text(s, rnd, "digits")
    elif trick == "invisible":
        s = invisible_text(s, rnd)
    elif trick == "digit_spaces":
        s = digit_spaces(s)
    elif trick == "linebreak":
        i = digit_cut(s)
        s = s[:i] + "\n" + s[i:]
    elif trick == "cell_split":
        i = digit_cut(s)
        eid = d.nid()
        if kind == "n":
            return [d.N(s[:i], why, type_, form, axes, trick=trick, nid=eid,
                        kind=neg_kind),
                    d.N(s[i:], why, type_, form, axes, trick=trick, nid=eid,
                        kind=neg_kind)]
        return [d.E(s[:i], type_, "adversarial", trick=trick, note=note, eid=eid,
                    form=form, axes=axes),
                d.E(s[i:], type_, "adversarial", trick=trick, eid=eid,
                    form=form, axes=axes)]
    else:
        raise ValueError(trick)

    if kind == "n":
        return [d.N(s, why, type_, form, axes, trick=trick, bs=bs,
                    kind=neg_kind)]
    return [d.E(s, type_, "adversarial", trick=trick, note=note, form=form,
                bs=bs, axes=axes)]


# Приёмы, выполнимые в .txt (там нет ни ранов, ни ячеек).
TXT_TRICKS = ["homoglyph", "invisible", "digit_spaces", "linebreak"]
DOCX_TRICKS = TXT_TRICKS + ["cell_split"]

TRICK_NOTE = {
    "words_mismatch": "цифры и пропись обозначают РАЗНЫЕ величины",
    "format_split": "число разорвано форматированием: половина цифр в одном ране, "
                    "половина в другом",
    "homoglyph": "кириллические О/З/Ч на месте цифр 0/3/4",
    "invisible": "NBSP/ZWSP/SHY внутри величины",
    "digit_spaces": "пробелы внутри цифр",
    "linebreak": "величина разорвана переводом строки",
    "cell_split": "число разорвано границей ячейки таблицы",
}


def adversarial_values_block(d, doc_no):
    """Раздел состязательных записей НОВЫХ видов данных.

    Есть в КАЖДОМ документе обеих групп структуры: иначе приёмы жили бы только
    там, где включён соответствующий флаг, а метрика новых видов на чистой
    группе так и осталась бы измеренной по чистому тексту.
    """
    L = [para([]), para([d.t("9. КОНТРОЛЬНЫЕ ВЕЛИЧИНЫ")], style="bold")]
    n = 0
    docx = d.format == "docx"
    types = list(V.NUMERIC_TYPES)

    if docx:
        # Разрыв форматированием — трижды, на трёх разных видах данных.
        for j in range(3):
            t = types[(doc_no + j) % len(types)]
            n += 1
            L.append(para([d.t("9.%d. Контрольная запись: " % n)]
                          + _adv_apply(d, d.adv_val(t), "format_split",
                                       TRICK_NOTE["format_split"])
                          + [d.t(".")]))
        others = [DOCX_TRICKS[doc_no % len(DOCX_TRICKS)]]
    else:
        # ЧЁТНОСТЬ. Формат документа выбран по чётности сквозного номера
        # (docx — нечётные), поэтому у .txt-документов `doc_no` всегда чётный,
        # и остаток от деления на 4 принимал бы только значения 0 и 2:
        # половина приёмов не встретилась бы в .txt НИ РАЗУ. Делим номер
        # пополам — так .txt-документы нумеруются подряд.
        j = doc_no // 2
        others = [TXT_TRICKS[j % len(TXT_TRICKS)],
                  TXT_TRICKS[(j + 2) % len(TXT_TRICKS)]]

    for j, tr in enumerate(others):
        t = types[(doc_no + 3 + j) % len(types)]
        chunks = _adv_apply(d, d.adv_val(t), tr, TRICK_NOTE[tr])
        n += 1
        if tr == "cell_split":
            # Граница ячейки — это и есть таблица: половина числа в одной
            # ячейке, половина в соседней.
            L.append(para([d.t("9.%d. Контрольная запись (таблица):" % n)]))
            L.append(table([[cell([para([chunks[0]])]), cell([para([chunks[1]])])]]))
        else:
            L.append(para([d.t("9.%d. Контрольная запись: " % n)] + chunks
                          + [d.t(".")]))

    # Цифры против прописи — каждый четвёртый документ. Приём текстовый, ему
    # безразличен формат, поэтому он есть и в .txt, и в .docx. Реже прочих
    # намеренно: он даёт ДВА спана за раз, и на каждом документе он перекосил
    # бы вид данных MONEY вхождениями одного происхождения.
    if doc_no % 4 == 0:
        n += 1
        parts = V.money_mismatch(d._form_cursor.setdefault("MONEY", 0), doc_no, d.rnd)
        d._form_cursor["MONEY"] += 1
        out = []
        for p in parts:
            if p[0] == "t":
                out.append(d.t(p[1]))
            else:
                _, s, type_, form, note, cat, axes = p
                out.append(d.E(s, type_, cat, trick="words_mismatch", note=note,
                               form=form, axes=axes))
        L.append(para([d.t("9.%d. Цена по протоколу разногласий: " % n)] + out
                      + [d.t(".")]))
    return L


# --------------------------------------------------------------------- клаузы
def negatives_clause(d):
    rnd = d.rnd
    ch = [d.t("Товар поставляется в соответствии с "),
          d.N("ГОСТ 7.32-2017", "стандарт — не ПДн"),
          d.t(", артикул "),
          d.N("RM-%d-%d" % (rnd.randint(1000, 9999), rnd.randint(10, 99)), "артикул — не ПДн"),
          d.t(", код "), d.N("ОКВЭД 46.90", "код ОКВЭД — не ПДн"),
          d.t(". Оплата по "),
          d.N("КБК 18210101011011000110", "КБК (20 цифр, похоже на счёт) — не ПДн"),
          d.t(", "), d.N("ОКТМО 45382000", "ОКТМО — не ПДн"),
          d.t(". См. "), d.N("Приложение № 1", "номер приложения — не ПДн"),
          d.t(" и "), d.N("счёт-фактуру № 1123 от 03.03.2024", "номер счёта-фактуры — не ПДн"),
          d.t(". Ответственный — "),
          d.N("Директор Отдела Продаж", "должность, похожая на ФИО, — не ПДн"),
          d.t(". Приёмка оформляется актом ("),
          d.N("Акт приёма-передачи", "название документа — не ПДн"),
          d.t("), срок — до 18:00 по "),
          d.N("московскому времени", "часовой пояс — не ПДн"),
          d.t(". Споры — в "),
          d.N("МКАС при ТПП РФ", "арбитражный институт — не ПДн"),
          # ЭТАП A1 — типизированный негатив PASSPORT: 10 цифр в форме
          # «SS SS NNNNNN» рядом со словом «паспорт», но это паспорт КАЧЕСТВА
          # товара, а не документ личности. До A2 ловился 138 из 138 (A1-
          # PASSPORT-QUALITY-FP); ЭТАП A2 добавил anti_anchor в entity_types.yaml
          # — теперь ДОЛЖЕН отсекаться. Плюс родня из части 2 (изделие, ТС,
          # технический — качество СЛЕВА от якоря, единственный случай, когда
          # дисквалификатор идёт ПЕРЕД словом «паспорт», а не после).
          d.t(". Товар отгружается с сопроводительным документом паспорт качества № "),
          d.N("%02d %02d %06d" % (rnd.randint(1, 99), rnd.randint(1, 25), rnd.randint(1, 999999)),
              "паспорт КАЧЕСТВА товара, не документа личности — 10 цифр в форме серии+номера рядом со словом «паспорт»",
              type_="PASSPORT"),
          d.t(". К партии приложен паспорт изделия № "),
          d.N("%02d %02d %06d" % (rnd.randint(1, 99), rnd.randint(1, 25), rnd.randint(1, 999999)),
              "паспорт ИЗДЕЛИЯ, не документа личности",
              type_="PASSPORT"),
          d.t(". Оборудование поставляется с паспортом транспортного средства № "),
          d.N("%02d %02d %06d" % (rnd.randint(1, 99), rnd.randint(1, 25), rnd.randint(1, 999999)),
              "паспорт ТРАНСПОРТНОГО СРЕДСТВА (ПТС), не документа личности",
              type_="PASSPORT"),
          d.t(". Приёмка производится согласно техническому паспорту "),
          d.N("%02d %02d %06d" % (rnd.randint(1, 99), rnd.randint(1, 25), rnd.randint(1, 999999)),
              "ТЕХНИЧЕСКИЙ паспорт — дисквалификатор ПЕРЕД якорем, не документ личности",
              type_="PASSPORT"),
          # ЭТАП A2 — типизированные негативы SNILS/BIRTHDATE/код подразделения:
          # якорь рядом, но число — не то значение. Зазоры подобраны НАРОЧНО
          # ВНУТРИ расширенного в этом же этапе допуска (см. A1-RIGID-GAP-
          # SURVEY) — это стресс-тест самого расширения, а не формальность.
          d.t(". СНИЛС не указан, номер заказа "),
          d.N("%03d-%03d-%03d %02d" % (rnd.randint(1, 999), rnd.randint(1, 999),
                                        rnd.randint(1, 999), rnd.randint(0, 99)),
              "номер заказа в форме СНИЛС рядом со словом «снилс», но это не СНИЛС",
              type_="SNILS"),
          d.t(". Дата рождения не проставлена, документ выдан "),
          d.N("%02d.%02d.%d" % (rnd.randint(1, 28), rnd.randint(1, 12), rnd.randint(2015, 2024)),
              "дата ВЫДАЧИ документа рядом со словом «рождения», но это не дата рождения человека",
              type_="BIRTHDATE"),
          d.t(". Код подразделения, см. артикул "),
          d.N("%03d-%03d" % (rnd.randint(1, 999), rnd.randint(1, 999)),
              "артикул в форме кода подразделения рядом с якорем, но это не код подразделения",
              type_="PASSPORT"),
          # В старом корпусе сумма стояла здесь НЕГАТИВОМ («сумма — не ПДн»).
          # В V2 сумма — полноценный вид данных, поэтому она размечена.
          d.t(". Цена — ")] + d.P(d.val("MONEY")) + [d.t(".")]
    return para(ch)


# ЭТАП A3 — ТИПИЗИРОВАННЫЕ НЕГАТИВЫ ДЛЯ ТИПОВ, У КОТОРЫХ ИХ НЕ БЫЛО ВОВСЕ.
#
# Повод — находка A2-NO-NEGATIVES-13-TYPES: 13 из 18 маскируемых типов не имели
# НИ ОДНОГО типизированного негатива ни в одном корпусе, поэтому линия «а» гейта
# (точность) по ним не могла показать ничего: ложное срабатывание считается
# только на ОБЪЯВЛЕННОМ негативе, а объявлять было нечего.
#
# ИСТОЧНИК ФОРМ — практика оформления российских договоров (штрихкод EAN-13 на
# товаре, КБК в платёжном условии, ОКПО контрагента, инвентарный номер, ссылка
# на статью ГК, название закона в кавычках, подсудность), а НЕ целевой документ
# владельца и не подгонка под конкретный паттерн. Ни одна из этих конструкций не
# является персональными данными, и маска на любой из них — порча документа.
#
# ГДЕ КЛАССА НЕТ — так и написано в `why`, отдельной пустышки не заводится
# (см. запись этапа в JOURNAL: PHONE/EMAIL, классы «якорь в другом значении»).
def negatives_a3(d):
    """Негативы этапа A3. Возвращает СПИСОК абзацев (по теме), а не один:
    сваленные в кучу, они читаются хуже, а разорвать негатив границей абзаца
    здесь незачем — приёмы разрыва живут на своей оси."""
    rnd = d.rnd
    out = []

    def NN(text, why, type_, **kw):
        """Двойник: похож на вид данных, им не является. `lookalike=True`
        держится ЗДЕСЬ, а не в каждом вызове, — иначе один пропущенный флаг
        тихо утащил бы двойника в покрытие осей."""
        return d.N(text, why, type_=type_, lookalike=True, **kw)

    # --- реквизиты-двойники: цифры нужной длины и формы, назначение другое ---
    out.append(para([
        d.t("Товар маркирован штрихкодом "),
        NN("460%010d" % rnd.randrange(10 ** 10),
            "штрихкод EAN-13 товара: 13 цифр, та же длина, что у ОГРН; "
            "контрольная сумма ОГРН на произвольных 13 цифрах сходится с "
            "вероятностью 1/13 (класс Stage4-C)",
            type_="OGRN"),
        d.t(". Код контрагента по ОКПО — "),
        NN("%010d" % rnd.randrange(10 ** 10),
            "ОКПО индивидуального предпринимателя: 10 цифр, та же длина, что у "
            "ИНН организации, якоря «ИНН» рядом нет — единственная защита здесь "
            "контрольная сумма ИНН, а она сходится случайно с вероятностью 1/10",
            type_="INN"),
        d.t(". Оборудование учтено под инвентарным номером "),
        NN("04%07d" % rnd.randrange(10 ** 7),
            "внутренний инвентарный номер: 9 цифр, начинается на 04 — ровно "
            "форма БИК, а у БИК нет ни якоря, ни контрольной суммы",
            type_="BIK"),
        d.t(", складской позиции присвоен номер "),
        NN("%09d" % rnd.randrange(10 ** 9),
            "складской номер: 9 цифр — ровно форма КПП, а у КПП нет ни якоря, "
            "ни контрольной суммы (тот же класс, что DOG-KPP-NOANCHOR)",
            type_="KPP"),
        d.t("."),
    ]))

    # --- слово-якорь в другом значении ---
    # ЗАЗОР «якорь -> значение» ЗДЕСЬ НАРОЧНО КОРОТКИЙ (меньше 40 символов —
    # `regex_detector._ANCHOR_WINDOW`). Это не подгонка, а условие самого класса:
    # «цифры другого назначения РЯДОМ с похожим якорем». Первая редакция этих
    # негативов ставила значение за 45-48 символов от якоря, якорь до него не
    # доставал, и все три давали ноль — то есть класс не проверялся вовсе, а
    # выглядел проверенным. См. запись этапа в JOURNAL.
    out.append(para([
        d.t("Оплата по счёту-фактуре на КБК "),
        NN("182101010110110001%02d" % rnd.randint(10, 99),
            "КБК: 20 цифр — та же длина, что у банковского счёта, и слово "
            "«счёт» рядом есть, но это счёт-ФАКТУРА (документ), а не банковский "
            "счёт; якорь ACCOUNT `сч[её]т\\w*` слово «счёту-фактуре» покрывает",
            type_="ACCOUNT"),
        d.t(". ИНН не присвоен, регистрационный № "),
        NN("%010d" % rnd.randrange(10 ** 10),
            "регистрационный номер иностранной организации: 10 цифр рядом с "
            "якорем «ИНН», но это не ИНН — под якорем контрольная сумма ИНН "
            "перестаёт быть фильтром (инвариант этапа 4)",
            type_="INN"),
        d.t(". ИНН не присвоен, платёжный документ № "),
        NN("%012d" % rnd.randrange(10 ** 12),
            "номер платёжного документа: 12 цифр рядом с якорем «ИНН», но это "
            "не ИНН физлица",
            type_="INN_PER"),
        d.t(". Заказчик действует в лице "),
        NN("уполномоченного представителя",
            "«в лице» — якорь ФИО (PerAnchorDetector), но за ним стоит "
            "нарицательное обозначение, а не имя человека",
            type_="PER"),
        d.t(", назначаемого приказом."),
    ]))

    # --- реквизиты самого документа и ссылки на нормы ---
    out.append(para([
        d.t("Массовая доля влаги в товаре — "),
        NN("не более %d %%" % rnd.randint(8, 16),
            "технический параметр товара. Решение владельца (этап T2) — "
            "маскировать СТАВКИ; влажность ставкой не является, и маска на ней "
            "искажает спецификацию, ничего не защищая",
            type_="PERCENT"),
        d.t(". Товар подлежит хранению "),
        NN("в течение %d часов" % rnd.choice([12, 24, 48]),
            "условие хранения товара, а не срок обязательства Стороны — "
            "конструкция «в течение N часов» опознаётся якорем формы, а не "
            "смыслом",
            type_="TERM"),
        d.t(" при температуре не выше +4 °C. Срок исковой давности по требованиям, "
            "вытекающим из Договора, составляет "),
        NN("в течение трёх лет",
            "срок из НОРМЫ ПРАВА (статья 196 ГК РФ), а не условие Договора; "
            "маска на нём делает нечитаемой ссылку на закон",
            type_="TERM"),
        d.t(", как установлено статьёй 196 Гражданского кодекса Российской Федерации. "
            "Обработка данных ведётся согласно Федеральному закону от 27.07.2006 № 152-ФЗ "),
        NN("«О персональных данных»",
            "название закона в кавычках. Кавычная ветвь ORG отличает название "
            "организации от роли по примыкающей org-форме; у названия закона "
            "org-формы нет, и он не должен становиться ORG",
            type_="ORG"),
        d.t(". Товар поставляется под маркой "),
        NN("«Стандарт»",
            "марка товара в кавычках — не организация",
            type_="ORG"),
        d.t(". Влажность определяется "),
        NN("по методу Кьельдаля",
            "фамилия учёного в названии лабораторного метода — не сторона "
            "договора и не ПДн",
            type_="PER"),
        d.t(". Споры передаются на рассмотрение в Арбитражный суд "),
        NN("города Москвы",
            "подсудность: топоним в названии суда — не адрес стороны; "
            "AddrExtractor топоним видит, а разницы «чей это адрес» не знает",
            type_="ADDRESS"),
        d.t("."),
    ]))

    # --- значение, разорванное так, что захват соседнего текста вероятен ---
    # У PHONE НЕТ ЯКОРЯ ВООБЩЕ: паттерн берёт любой 11-значный прогон, начатый
    # 8/+7. Класса «слово-якорь в другом значении» у него поэтому не существует,
    # а честного 11-значного НЕтелефонного реквизита в практике российских
    # договоров не нашлось (ОКПО 8/10, КПП 9, ОКТМО 8/11 — но 11-значный ОКТМО
    # начинается с кода региона, и подобрать реальный код, начинающийся на 8,
    # значило бы выдумать его). Остаётся класс СКЛЕЙКИ, и он не выдуман:
    # окно детекции склеивает ячейки одной строки БЕЗ разделителя (находка B3),
    # поэтому номер строки «8» и 10-значный номер в соседней ячейке образуют
    # ровно форму телефона. Обе ячейки — ОДИН негатив (общий nid).
    nid = "%s-a3phone" % d.doc_id
    out.append(table([[
        cell([para([NN("8", "номер строки реестра, слипающийся с 10-значным "
                        "номером в соседней ячейке: окно детекции склеивает "
                        "ячейки строки без разделителя (B3), и получается "
                        "11-значный прогон формы телефона",
                        type_="PHONE", nid=nid)])]),
        cell([para([NN("%010d" % rnd.randrange(10 ** 10),
                        "вторая половина той же склейки — реестровый номер "
                        "позиции, не телефон",
                        type_="PHONE", nid=nid)])]),
    ]]))
    return out


# ЭТАП A7 — негативы на ПЯТЬ ПРАВОК ЭТОГО ЭТАПА. Заведены отдельной функцией,
# а не дописаны в negatives_a3: там негативы «типов, у которых их не было
# вовсе», здесь — «форм, которые A7 впервые сделал достижимыми». Правило то же
# и оно не смягчено: расширил класс форм — заведи двойника этого класса, иначе
# переусердствование останется невидимым.
def negatives_a7(d):
    rnd = d.rnd
    out = []

    def NN(text, why, type_, **kw):
        return d.N(text, why, type_=type_, lookalike=True, **kw)

    # --- (долг 4) КБК, РАЗБИТЫЙ ПО ТРИ ЦИФРЫ. Отличие от негатива A3 (тот же
    # КБК слитно) принципиальное: слитные 20 цифр паттернам ИНН/ОГРН недоступны
    # вовсе ( на обоих концах), а разбитый пробелами открывает ОКНА по
    # границам групп — 15-значное окно 0..15 внутри этого КБК проходит
    # контрольную сумму ОГРНИП. Ровно класс Stage4-C, и до A7 он давал маску.
    out.append(para([
        d.t("Платёж по налогу перечисляется на код бюджетной классификации "),
        NN("182 101 010 110 110 001 10",
            "КБК, записанный группами по три цифры (обычная вёрстка платёжного "
            "реквизита): 15-значное окно внутри него ложится на границу групп и "
            "проходит контрольную сумму ОГРНИП — но это кусок ЧУЖОГО числа, а "
            "не ОГРНИП стороны (класс Stage4-C / A5-INN-KBK-FP)",
            type_="OGRN"),
        d.t(", получатель — территориальный орган Федерального казначейства."),
    ]))

    # --- (долг 3) ДАТА ПОД ЯКОРЕМ РОЖДЕНИЯ, НО НЕ ДАТА РОЖДЕНИЯ. Класс не
    # изобретён под правку: якорь `рожд(ени[а-я]*|\.)` покрывает и «о рождении»
    # в названии документа, а за ним в практике стоит дата ВЫДАЧИ.
    out.append(para([
        d.t("Полномочия подтверждаются свидетельством о рождении, выдано "),
        NN("%02d.%02d.20%02d" % (rnd.randint(1, 28), rnd.randint(1, 12), rnd.randint(10, 24)),
            "дата ВЫДАЧИ документа, стоящая под якорем «о рождении»: якорь "
            "BIRTHDATE опознаёт слово, а не смысл; маска здесь — порча даты "
            "документа, а не защита ПДн",
            type_="BIRTHDATE"),
        d.t(" отделом ЗАГС."),
    ]))

    # --- (долг 1) РЕГИСТР В СУММАХ. `(?i)` сделал достижимой шапку таблицы,
    # набранную прописными. Достижимой стала и СКЛЕЙКА ячеек строки (B3, окно
    # соединяет ячейки одной строки БЕЗ разделителя): номер позиции в первой
    # ячейке и единица измерения «РУБ.» во второй дают форму суммы. Обе ячейки —
    # ОДИН негатив (общий nid), как у телефонной склейки этапа A3.
    nid = "%s-a7sum" % d.doc_id
    out.append(table([[
        cell([para([NN("%d" % rnd.randint(2, 9),
                       "номер позиции спецификации, слипающийся с единицей "
                       "измерения в соседней ячейке: окно детекции склеивает "
                       "ячейки строки без разделителя (B3), и получается форма "
                       "суммы «5РУБ.» — до снятия регистра у SUM недостижимая",
                       type_="MONEY", nid=nid)])]),
        cell([para([NN("РУБ.",
                       "вторая половина той же склейки — заголовок единицы "
                       "измерения, набранный прописными, а не сумма",
                       type_="MONEY", nid=nid)])]),
    ]]))

    # --- (долг 2) СВОД АЛФАВИТОВ, ЗАМКНУТЫЙ ПО РЕГИСТРУ. Замыкание чинит слово
    # СМЕШАННОГО алфавита — а такие слова бывают не только именами: марки,
    # модели и артикулы пишут кириллицей с латинскими вставками. Свод превратит
    # их в кириллические, и NER получит слово, которого в документе не было.
    out.append(para([
        d.t("Поставке подлежит насос модели "),
        NN("ЭЦВ-6-10-80h",
            "обозначение модели оборудования: кириллица с латинской вставкой — "
            "тот же признак «смешанный алфавит», по которому работает свод "
            "латиница->кириллица; после свода это «...80н», и слово не должно "
            "стать ни ФИО, ни организацией",
            type_="PER"),
        d.t(" и кабель марки "),
        NN("ВВГнг(А)-LSt",
            "марка кабеля по ГОСТ: та же конструкция со стороны ORG — "
            "смешанный алфавит внутри технического обозначения",
            type_="ORG"),
        d.t("."),
    ]))

    return out


# ЭТАП NEG — ЧЕСТНЫЙ НАБОР ОТРИЦАТЕЛЬНЫХ ПРИМЕРОВ ДЛЯ ВОСЬМИ ТИПОВ.
#
# ПОВОД (оговорка A3, docs/FINDINGS.md). Замер A3 дал по девяти типам «138 из
# 138», но за этим числом стояла ОДНА конструкция, повторённая в 138 документах:
# по одному виду случая на тип, по одному вхождению на документ, и левое
# окружение у 8 из 16 видов было ровно одно. Чинить по таким числам нельзя —
# починится ровно тот случай, который автор корпуса сам и придумал. KPP из
# девяти закрыт этапом A4; остаётся восемь: BIK, ACCOUNT, INN, INN_PER, PHONE,
# PERCENT и два вида TERM.
#
# ЧТО ЗДЕСЬ ДЕЛАЕТСЯ. На каждый из восьми — ПЯТЬ РАЗНЫХ ВИДОВ СЛУЧАЯ, а не пять
# вариаций одного, покрывающих классы задания:
#   * цифры нужной длины ДРУГОГО НАЗНАЧЕНИЯ (накладная, артикул, КБК, УИН,
#     лицевой счёт ЖКХ, серийный номер);
#   * РЕКВИЗИТЫ САМОГО ДОКУМЕНТА (номер приложения, спецификации, акта);
#   * ССЫЛКИ НА НОРМЫ (статья, пункт, федеральный закон, приказ);
#   * СЛОВО-ЯКОРЬ В ДРУГОМ ЗНАЧЕНИИ (предлог «за счёт», «счётчик», «БИК не
#     указан», «ИНН не присвоен»);
#   * ЧИСЛА В ТЕХНИЧЕСКИХ ПАРАМЕТРАХ (доля примеси, погрешность, уклон,
#     выдержка в часах).
#
# РАЗНЫЕ ОКРУЖЕНИЯ И РАЗНЫЕ ПОЗИЦИИ. Левая связка у каждого случая выбирается
# `rnd.choice` из нескольких формулировок — значит в корпусе у одного вида
# случая не одно левое окно, а несколько, и «138 из 138» перестаёт быть
# свойством единственной фразы. Абзацы раздаются в ТРИ разных места документа
# (предмет, место исполнения, прочие условия), а не сваливаются в раздел 2
# следом за негативами A3.
#
# ИСТОЧНИК ФОРМ — практика оформления российских договоров. Ни одна из этих
# конструкций персональными данными не является, маска на любой — порча
# документа.
def negatives_neg(d):
    """Негативы этапа NEG. Возвращает три списка абзацев — по одному на место
    вставки в документе (см. `build`)."""
    rnd = d.rnd

    def NN(text, why, type_, kind, **kw):
        return d.N(text, why, type_=type_, lookalike=True, kind=kind, **kw)

    def pick(*variants):
        return rnd.choice(variants)

    # 9 цифр, начатых «04» — ровно форма БИК (у типа нет ни якоря, ни КС).
    bik = lambda: "04%07d" % rnd.randrange(10 ** 7)
    # 20 цифр — длина банковского счёта (у типа якорь `сч[её]т\w*`, КС нет).
    d20 = lambda p: p + "%0*d" % (20 - len(p), rnd.randrange(10 ** (20 - len(p))))
    d10 = lambda: "%010d" % rnd.randrange(10 ** 10)
    d12 = lambda: "%012d" % rnd.randrange(10 ** 12)
    # 11 цифр, начатых «8» — форма телефона (у типа нет ни якоря, ни КС).
    d11 = lambda: "8%010d" % rnd.randrange(10 ** 10)

    # ---------------------------------------------------------------- блок 1
    # БИК и ТЕЛЕФОН — типы БЕЗ ЯКОРЯ И БЕЗ КОНТРОЛЬНОЙ СУММЫ. Защиты у них нет
    # вовсе, поэтому им первый блок и самый широкий разброс окружений.
    b1 = []
    b1.append(para([
        d.t(pick("Товар отгружается по товарной накладной ТОРГ-12 № ",
                 "Отгрузка оформляется товарной накладной № ",
                 "Партия сопровождается накладной формы ТОРГ-12 № ")),
        NN(bik(), "номер товарной накладной ТОРГ-12: 9 цифр, начатых «04», — "
                  "ровно форма БИК; это реквизит СОПРОВОДИТЕЛЬНОГО ДОКУМЕНТА, "
                  "а не банковский идентификационный код",
           "BIK", "bik/doc-number"),
        d.t(pick(". Позиция учитывается в каталоге поставщика под номером ",
                 ". Номенклатурный номер позиции — ",
                 ". Каталожный номер изделия — ")),
        NN(bik(), "каталожный (номенклатурный) номер позиции: цифры нужной "
                  "длины другого назначения — учётная номенклатура поставщика",
           "BIK", "bik/catalogue"),
        d.t(pick(". Заводской номер насоса — ",
                 ". Серийный номер агрегата — ",
                 ". Изделию присвоен заводской номер ")),
        NN(bik(), "заводской (серийный) номер оборудования — число в "
                  "техническом параметре изделия, не реквизит стороны",
           "BIK", "bik/serial"),
        d.t("."),
    ]))
    b1.append(para([
        d.t(pick("Целевые средства перечисляются по коду цели ",
                 "Расходы отражаются по коду цели ",
                 "Финансирование ведётся по коду целевых средств ")),
        NN(bik(), "код цели расходования средств, введённый приказом Минфина: "
                  "9 цифр вида БИК внутри ССЫЛКИ НА НОРМУ",
           "BIK", "bik/norm-ref"),
        d.t(pick(", установленному приказом Минфина России от 01.12.2010 № 157н. ",
                 ", предусмотренному приказом Минфина России № 157н. ",
                 " согласно приказу Минфина России № 157н. ")),
        d.t(pick("БИК банка Стороны в настоящем разделе не приводится; номер заявки на "
                 "открытие счёта — ",
                 "БИК не указан, номер заявки — ",
                 "БИК будет сообщён дополнительно, номер заявки — ")),
        NN(bik(), "СЛОВО-ЯКОРЬ «БИК» В ДРУГОМ ЗНАЧЕНИИ: рядом стоит не сам код, "
                  "а номер заявки. Этот вид случая проверяет цену возможной "
                  "правки «завести якорь там, где его нет»: под якорем "
                  "безосновательное число снова станет маской",
           "BIK", "bik/anchor-other"),
        d.t("."),
    ]))
    b1.append(para([
        d.t(pick("Расчёты за коммунальные услуги ведутся по лицевому счёту абонента ",
                 "Абоненту открыт лицевой счёт ",
                 "Учёт потребления ведётся по лицевому счёту ")),
        NN(d11(), "лицевой счёт абонента ЖКХ: 11 цифр, начатых «8», — ровно "
                  "форма телефона; у PHONE нет ни якоря, ни контрольной суммы",
           "PHONE", "phone/personal-account"),
        d.t(pick(". Приложение к настоящему разделу зарегистрировано за номером ",
                 ". Спецификация зарегистрирована за номером ",
                 ". Приложение учтено за номером ")),
        NN(d11(), "регистрационный номер приложения к Договору — реквизит "
                  "самого документа, а не номер телефона",
           "PHONE", "phone/doc-number"),
        d.t(pick(". Прибор учёта имеет заводской номер ",
                 ". Счётчик имеет заводской номер ",
                 ". Заводской номер прибора учёта — ")),
        NN(d11(), "заводской номер прибора учёта — число в техническом "
                  "паспорте оборудования",
           "PHONE", "phone/serial"),
        d.t("."),
    ]))
    b1.append(para([
        d.t(pick("Партия внесена в реестр под номером ",
                 "Сведения внесены в реестр за номером ",
                 "Регистрационная запись в реестре — ")),
        NN(d11(), "номер записи в отраслевом реестре: цифры нужной длины "
                  "другого назначения",
           "PHONE", "phone/registry"),
        d.t(pick(", как предусмотрено пунктом 2 статьи 434 Гражданского кодекса "
                 "Российской Федерации. Уведомление о регистрации имеет исходящий № ",
                 " в порядке статьи 434 Гражданского кодекса Российской Федерации. "
                 "Исходящий номер уведомления — ",
                 " по правилам статьи 434 Гражданского кодекса Российской Федерации. "
                 "Уведомление направлено за исходящим № ")),
        NN(d11(), "исходящий номер уведомления в предложении со ССЫЛКОЙ НА "
                  "НОРМУ — реквизит переписки, не телефон",
           "PHONE", "phone/norm-ref"),
        d.t("."),
    ]))

    # ---------------------------------------------------------------- блок 2
    # ACCOUNT / INN / INN_PER — типы С ЯКОРЕМ. Их слабое место другое: под
    # якорем контрольная сумма перестаёт быть фильтром (инвариант этапа 4), а
    # сам якорь опознаёт СЛОВО, а не смысл.
    b2 = []
    b2.append(para([
        d.t(pick("Работы выполняются за счёт средств бюджета по коду бюджетной "
                 "классификации ",
                 "Оплата производится за счёт бюджетных средств, КБК ",
                 "Финансирование ведётся за счёт средств бюджета, КБК ")),
        NN(d20("182"), "СЛОВО-ЯКОРЬ В ДРУГОМ ЗНАЧЕНИИ: «за счёт» — предлог, а "
                       "не банковский счёт; якорь ACCOUNT `сч[её]т\\w*` его "
                       "покрывает, а КБК имеет ровно длину счёта",
           "ACCOUNT", "account/prep-za-schet"),
        d.t(pick(". Показания счётчика снимаются по прибору с заводским номером ",
                 ". Счётчик имеет заводской номер ",
                 ". Прибор учёта (счётчик) № ")),
        NN(d20("39"), "СЛОВО-ЯКОРЬ В ДРУГОМ ЗНАЧЕНИИ: «счётчик» — прибор учёта; "
                      "якорь `сч[её]т\\w*` матчит и его, а 20-значный заводской "
                      "номер имеет длину банковского счёта",
           "ACCOUNT", "account/schetchik"),
        d.t("."),
    ]))
    b2.append(para([
        d.t(pick("Счёт на оплату выставляется с уникальным идентификатором начисления ",
                 "В счёте на оплату указывается УИН ",
                 "Счёт на оплату содержит УИН ")),
        NN(d20("18209"), "УИН (уникальный идентификатор начисления): 20 цифр — "
                         "ровно длина счёта, слово «счёт» рядом есть, но "
                         "относится к ДОКУМЕНТУ, а не к банковскому счёту",
           "ACCOUNT", "account/uin"),
        d.t(pick(". Счёт-проформа к настоящему Договору имеет номер ",
                 ". Номер счёта-проформы — ",
                 ". Счёт-проформа зарегистрирован за номером ")),
        NN(d20("77"), "номер счёта-проформы — РЕКВИЗИТ САМОГО ДОКУМЕНТА; "
                      "«счёт-проформа» покрывается тем же якорем",
           "ACCOUNT", "account/proforma"),
        d.t(pick(". Очерёдность списания со счёта определяется статьёй 855 "
                 "Гражданского кодекса Российской Федерации, код вида дохода — ",
                 ". Списание со счёта производится в очерёдности статьи 855 "
                 "Гражданского кодекса Российской Федерации, код вида дохода ",
                 ". В силу статьи 855 Гражданского кодекса Российской Федерации "
                 "списание со счёта ведётся по коду вида дохода ")),
        NN(d20("18210"), "код вида дохода в предложении со ССЫЛКОЙ НА НОРМУ: "
                         "слово «счёт» здесь принадлежит цитате из закона",
           "ACCOUNT", "account/norm-ref"),
        d.t("."),
    ]))
    b2.append(para([
        d.t(pick("ИНН иностранного контрагента не присваивается; номер записи в "
                 "торговом реестре — ",
                 "ИНН иностранному контрагенту не присваивается, номер в торговом "
                 "реестре ",
                 "ИНН у иностранного контрагента отсутствует, номер в торговом "
                 "реестре — ")),
        NN(d10(), "номер записи в иностранном торговом реестре под якорем «ИНН»: "
                  "под якорем контрольная сумма перестаёт быть фильтром "
                  "(инвариант этапа 4)",
           "INN", "inn/anchor-registry"),
        d.t(pick(". ИНН приводится в приложении; номер спецификации — ",
                 ". ИНН указан в приложении, номер спецификации ",
                 ". ИНН см. в приложении; спецификация № ")),
        NN(d10(), "номер спецификации — РЕКВИЗИТ САМОГО ДОКУМЕНТА, стоящий под "
                  "якорем «ИНН»",
           "INN", "inn/anchor-doc-number"),
        d.t(pick(". ИНН присваивается в порядке статьи 84 Налогового кодекса "
                 "Российской Федерации; номер решения о постановке на учёт — ",
                 ". Постановка на учёт производится по статье 84 Налогового "
                 "кодекса Российской Федерации, решение № ",
                 ". В силу статьи 84 Налогового кодекса Российской Федерации "
                 "ИНН присваивается налоговым органом; номер решения — ")),
        NN(d10(), "номер решения налогового органа в предложении со ССЫЛКОЙ НА "
                  "НОРМУ: слово «ИНН» здесь — часть цитаты из закона",
           "INN", "inn/norm-ref"),
        d.t("."),
    ]))
    b2.append(para([
        d.t(pick("Номер партии по системе прослеживаемости — ",
                 "Партии присвоен номер прослеживаемости ",
                 "Регистрационный номер партии товара — ")),
        NN(d10(), "номер партии в системе прослеживаемости: 10 цифр БЕЗ якоря — "
                  "единственная защита здесь контрольная сумма ИНН, а она "
                  "сходится случайно примерно в одном случае из десяти",
           "INN", "inn/noanchor-batch"),
        d.t(pick(". Массовая доля примеси в товаре — не более ",
                 ". Содержание примеси не превышает ",
                 ". Доля посторонних включений — не более ")),
        NN("%d,%d %%" % (rnd.randint(0, 2), rnd.randint(1, 9)),
           "массовая доля примеси — ЧИСЛО В ТЕХНИЧЕСКОМ ПАРАМЕТРЕ товара, а не "
           "ставка; маска искажает спецификацию, ничего не защищая",
           "PERCENT", "percent/impurity"),
        d.t(pick(". Погрешность измерения прибора — не более ",
                 ". Допустимая погрешность прибора — ",
                 ". Прибор имеет погрешность не более ")),
        NN("0,%d %%" % rnd.randint(1, 9),
           "погрешность средства измерения — технический параметр прибора",
           "PERCENT", "percent/tolerance"),
        d.t(pick(". Заводской номер поставляемого станка — ",
                 ". Станок имеет заводской номер ",
                 ". Оборудованию присвоен заводской номер ")),
        NN(d10(), "заводской номер станка (10 цифр, якоря «ИНН» рядом нет) — "
                  "число в техническом паспорте оборудования",
           "INN", "inn/serial"),
        d.t("."),
    ]))

    # ---------------------------------------------------------------- блок 3
    # PERCENT и TERM — типы, опознающие ФОРМУ, а не смысл. Их вид случая
    # «ссылка на норму» самый дорогой: маска делает нечитаемой отсылку к закону.
    b3 = []
    b3.append(para([
        d.t(pick("ИНН физического лица не указан; номер лицевого счёта абонента — ",
                 "ИНН физического лица отсутствует, лицевой счёт абонента ",
                 "ИНН физического лица не приводится; лицевой счёт — ")),
        NN(d12(), "номер лицевого счёта абонента (12 цифр) под якорем «ИНН»: "
                  "маска приходит пользователю в наборе ПО УМОЛЧАНИЮ, потому "
                  "что INN_PER входит в набор «только персональные данные»",
           "INN_PER", "inn_per/anchor-account"),
        d.t(pick(". ИНН указан в приложении; номенклатурный код позиции — ",
                 ". ИНН см. приложение; номенклатурный код — ",
                 ". ИНН приведён в приложении, код позиции ")),
        NN(d12(), "номенклатурный код позиции (12 цифр) под якорем «ИНН» — "
                  "цифры нужной длины другого назначения",
           "INN_PER", "inn_per/anchor-nomenclature"),
        d.t(pick(". ИНН физического лица обрабатывается в порядке статьи 86 "
                 "Налогового кодекса Российской Федерации; номер запроса — ",
                 ". Обработка ИНН ведётся по статье 86 Налогового кодекса "
                 "Российской Федерации, запрос № ",
                 ". В силу статьи 86 Налогового кодекса Российской Федерации "
                 "ИНН запрашивается налоговым органом; номер запроса — ")),
        NN(d12(), "номер запроса налогового органа в предложении со ССЫЛКОЙ НА "
                  "НОРМУ",
           "INN_PER", "inn_per/norm-ref"),
        d.t("."),
    ]))
    b3.append(para([
        d.t(pick("Товарная накладная на партию имеет номер ",
                 "Накладной на партию присвоен номер ",
                 "Номер товарной накладной на партию — ")),
        NN(d12(), "номер товарной накладной (12 цифр) БЕЗ якоря — реквизит "
                  "сопроводительного документа",
           "INN_PER", "inn_per/noanchor-doc"),
        d.t(pick(". Заводской номер контрольного модуля — ",
                 ". Контрольный модуль имеет заводской номер ",
                 ". Модуль учёта имеет заводской номер ")),
        NN(d12(), "заводской номер модуля — число в техническом параметре "
                  "изделия",
           "INN_PER", "inn_per/serial"),
        d.t(pick(". Уклон кровли поставляемого сооружения — ",
                 ". Уклон монтируемой кровли составляет ",
                 ". Проектный уклон кровли — ")),
        NN("%d %%" % rnd.randint(2, 9),
           "уклон кровли — технический параметр конструкции, выраженный в "
           "процентах; ставкой не является",
           "PERCENT", "percent/slope"),
        d.t("."),
    ]))
    b3.append(para([
        d.t(pick("Предельная доля иностранного участия в уставном капитале "
                 "установлена законом и составляет ",
                 "Законом установлена предельная доля иностранного участия — ",
                 "Предельная доля иностранного участия по закону — ")),
        NN("%d %%" % rnd.choice([20, 25, 49]),
           "предельная доля участия, установленная НОРМОЙ ПРАВА, а не Договором: "
           "маска на ней делает нечитаемой ссылку на закон",
           "PERCENT", "percent/norm-ref"),
        d.t(pick(", как предусмотрено Федеральным законом о порядке осуществления "
                 "иностранных инвестиций. Приложение № 3 к Договору содержит долю "
                 "брака, не превышающую ",
                 " в силу Федерального закона об иностранных инвестициях. В "
                 "приложении № 3 указана допустимая доля брака — ",
                 " согласно Федеральному закону об иностранных инвестициях. "
                 "Приложением № 3 установлена доля брака не более ")),
        NN("%d,%d %%" % (rnd.randint(0, 3), rnd.randint(1, 9)),
           "допустимая доля брака в приложении к Договору — показатель качества "
           "партии (РЕКВИЗИТ ДОКУМЕНТА-ПРИЛОЖЕНИЯ), не ставка",
           "PERCENT", "percent/defect-rate"),
        d.t("."),
    ]))
    b3.append(para([
        d.t(pick("Срок годности товара — ",
                 "Товар имеет срок годности ",
                 "Изготовитель установил срок годности ")),
        NN("в течение %d месяцев" % rnd.choice([12, 18, 24, 36]),
           "срок годности ТОВАРА — свойство вещи, а не срок обязательства "
           "Стороны; конструкция опознаётся якорем формы «в течение», а не "
           "смыслом",
           "TERM", "term/shelf-life"),
        d.t(pick(" с даты изготовления. Бетонная смесь выдерживается ",
                 " с даты выпуска. Смесь выдерживается ",
                 " с даты производства. Уложенная смесь выдерживается ")),
        NN("в течение %d часов" % rnd.choice([24, 48, 72]),
           "выдержка материала — ЧИСЛО В ТЕХНОЛОГИЧЕСКОМ ПАРАМЕТРЕ, не срок "
           "обязательства",
           "TERM", "term/tech-hold"),
        d.t(pick(" при температуре не ниже +5 °C.",
                 " при положительной температуре.",
                 " без доступа влаги.")),
    ]))
    b3.append(para([
        d.t(pick("Требования к качеству работ могут быть предъявлены ",
                 "Претензии по качеству работ предъявляются ",
                 "Требования по качеству работ заявляются ")),
        NN("в течение одного года",
           "срок предъявления требований, установленный НОРМОЙ ПРАВА "
           "(статья 725 Гражданского кодекса): маска делает нечитаемой ссылку "
           "на закон",
           "TERM", "term/norm-ref"),
        d.t(pick(", как установлено статьёй 725 Гражданского кодекса Российской "
                 "Федерации. Общество осуществляет деятельность ",
                 " в силу статьи 725 Гражданского кодекса Российской Федерации. "
                 "Поставщик работает на рынке ",
                 " по правилам статьи 725 Гражданского кодекса Российской "
                 "Федерации. Поставщик присутствует на рынке ")),
        NN("в течение %d лет" % rnd.randint(7, 25),
           "срок существования организации в справочном предложении — ОПИСАНИЕ, "
           "а не обязательство Стороны",
           "TERM", "term/history"),
        d.t(pick(". Отчётный период по Договору завершается ",
                 ". Отчётный период оканчивается ",
                 ". Окончание отчётного периода — ")),
        NN("не позднее %02d.%02d.202%d" % (rnd.randint(1, 28), rnd.randint(1, 12),
                                            rnd.randint(4, 9)),
           "граница ОТЧЁТНОГО ПЕРИОДА — реквизит самого документа (когда "
           "закрывается период), а не срок исполнения обязательства",
           "TERM", "term/report-period"),
        d.t("."),
    ]))

    return b1, b2, b3


# --------------------------------------------------------------------- клаузы
# КЛЮЧЕВОЕ ОТЛИЧИЕ ОТ ОРИГИНАЛА. Там клаузы и шум были готовыми строками, и
# спрятанные внутри них проценты («12 (двенадцать) процентов годовых») и сроки
# («в течение 3 (трёх) рабочих дней») координат не получали — разметка их не
# видела вовсе. Здесь клауза — ФУНКЦИЯ, возвращающая список частей: связки
# (V.T) и размеченные величины (d.val(...)). Величина берётся из реестра форм
# values.py, поэтому одна и та же клауза в разных документах несёт РАЗНЫЕ
# формы записи, а не одну заученную.
#
# СВЯЗКИ НАРОЧНО НЕЙТРАЛЬНЫЕ («срок оплаты — …», «Сумма займа: …»). Причина
# не в лени: форма записи в клаузе меняется от документа к документу, и связка
# вида «оплата производится …» ломалась бы грамматически на форме «7 рабочих
# дней», а «составляет … в месяц» — на форме «Без НДС». Кроме того, жёсткая
# связка означала бы, что каждая форма живёт ровно в одном синтаксическом
# окружении, и детектор мог бы выучить окружение вместо величины.
def _c(text):
    """Клауза без величин."""
    return lambda d: [V.T(text)]


CLAUSES = {
    "supply": [
        _c("Поставщик обязуется передать в собственность Покупателя товар, а Покупатель — принять товар и уплатить за него цену в порядке и сроки, предусмотренные Договором."),
        _c("Наименование, ассортимент, количество и цена товара определяются в спецификациях, являющихся неотъемлемой частью Договора."),
        lambda d: [V.T("Поставка осуществляется партиями на основании заявок Покупателя; срок отгрузки партии — ")]
                  + d.val("TERM") + [V.T(".")],
        _c("Право собственности на товар переходит к Покупателю с момента подписания товарной накладной уполномоченными представителями Сторон."),
        _c("Качество товара должно соответствовать требованиям технических регламентов и подтверждаться сертификатами соответствия."),
    ],
    "services": [
        _c("Исполнитель обязуется по заданию Заказчика оказать услуги, а Заказчик обязуется оплатить эти услуги."),
        _c("Перечень, объём и сроки оказания услуг согласовываются Сторонами в заданиях, оформляемых в письменной форме."),
        lambda d: [V.T("Услуги считаются оказанными после подписания Сторонами акта сдачи-приёмки. Срок оплаты — ")]
                  + d.val("TERM") + [V.T(".")],
        _c("Заказчик вправе в любое время проверять ход и качество оказываемых услуг, не вмешиваясь в деятельность Исполнителя."),
    ],
    "lease": [
        _c("Арендодатель обязуется передать Арендатору за плату во временное владение и пользование нежилое помещение."),
        lambda d: [V.T("Срок передачи помещения по акту приёма-передачи — ")] + d.val("TERM") + [V.T(".")],
        lambda d: [V.T("Арендная плата за месяц: ")] + d.val("MONEY")
                  + [V.T(". Плата вносится ежемесячно не позднее 10-го числа текущего месяца.")],
        _c("Арендатор обязан поддерживать помещение в исправном состоянии и производить текущий ремонт за свой счёт."),
    ],
    "works": [
        _c("Подрядчик обязуется выполнить по заданию Заказчика работы и сдать их результат, а Заказчик — принять результат работ и оплатить его."),
        _c("Работы выполняются иждивением Подрядчика — из его материалов, его силами и средствами."),
        _c("Сдача результата работ оформляется актом по форме КС-2 и справкой по форме КС-3."),
        lambda d: [V.T("Гарантийный срок на результат работ — ")] + d.val("TERM") + [V.T(".")],
    ],
    "loan": [
        _c("Займодавец передаёт в собственность Заёмщику денежные средства, а Заёмщик обязуется возвратить сумму займа в срок и в порядке, установленные Договором."),
        lambda d: [V.T("Сумма займа: ")] + d.val("MONEY")
                  + [V.T(". Заём считается возвращённым в момент зачисления денежных средств на счёт Займодавца.")],
        lambda d: [V.T("Ставка за пользование займом: ")]
                  + d.val("PERCENT") + [V.T(".")],
        lambda d: [V.T("Выдача займа производится траншами; ")] + d.val("TRANCHE")
                  + [V.T(" согласуется Сторонами дополнительно.")],
    ],
    "cession": [
        _c("Цедент уступает, а Цессионарий принимает право требования к должнику, возникшее из договора поставки."),
        _c("Цедент обязан передать Цессионарию все документы, удостоверяющие уступаемое право требования."),
        lambda d: [V.T("Уступка права требования является возмездной. Цена уступаемого права: ")]
                  + d.val("MONEY") + [V.T(".")],
        _c("Цедент отвечает за недействительность переданного требования, но не отвечает за неисполнение обязательства должником."),
    ],
    "agency": [
        _c("Агент обязуется за вознаграждение совершать по поручению Принципала юридические и иные действия от своего имени, но за счёт Принципала."),
        _c("Агент представляет Принципалу отчёт по мере исполнения поручения, но не реже одного раза в месяц."),
        lambda d: [V.T("Агентское вознаграждение: ")] + d.val("PERCENT")
                  + [V.T(". Вознаграждение исчисляется от суммы заключённых сделок.")],
        _c("Принципал обязан возместить Агенту расходы, понесённые при исполнении поручения."),
    ],
    "labor": [
        _c("Работник принимается на работу на должность инженера-технолога в производственный отдел."),
        _c("Работа по настоящему Договору является для Работника основным местом работы."),
        _c("Работнику устанавливается пятидневная рабочая неделя с двумя выходными днями."),
        lambda d: [V.T("Должностной оклад Работника: ")] + d.val("MONEY")
                  + [V.T(". Заработная плата выплачивается не реже чем каждые полмесяца.")],
        lambda d: [V.T("Ежегодный оплачиваемый отпуск: ")]
                  + d.val("TERM") + [V.T(".")],
    ],
    "sale": [
        _c("Продавец обязуется передать в собственность Покупателя имущество, а Покупатель — принять его и уплатить цену."),
        _c("Продавец гарантирует, что имущество не заложено, не находится под арестом и свободно от прав третьих лиц."),
        _c("Право собственности переходит к Покупателю с момента государственной регистрации перехода права."),
        lambda d: [V.T("Расчёты производятся через аккредитив, открываемый Покупателем. Сумма аккредитива: ")]
                  + d.val("MONEY") + [V.T(".")],
    ],
}

NOISE = [
    _c("Стороны несут ответственность за неисполнение или ненадлежащее исполнение обязательств в соответствии с законодательством Российской Федерации."),
    lambda d: [V.T("Размер неустойки за нарушение сроков оплаты: ")]
              + d.val("PERCENT") + [V.T(". Предельный размер ответственности: ")]
              + d.val("PERCENT") + [V.T(".")],
    _c("Стороны освобождаются от ответственности при наступлении обстоятельств непреодолимой силы, если они прямо повлияли на исполнение обязательств."),
    lambda d: [V.T("Сторона, для которой создалась невозможность исполнения, обязана уведомить другую Сторону. Срок уведомления — ")]
              + d.val("TERM") + [V.T(".")],
    lambda d: [V.T("Все споры и разногласия решаются путём переговоров. Претензионный порядок обязателен, срок ответа на претензию — ")]
              + d.val("TERM") + [V.T(".")],
    _c("Договор вступает в силу с момента подписания и действует до полного исполнения Сторонами принятых обязательств."),
    _c("Изменения и дополнения к Договору действительны при условии их совершения в письменной форме и подписания уполномоченными представителями Сторон."),
    _c("Договор составлен в двух экземплярах, имеющих равную юридическую силу, по одному для каждой из Сторон."),
    _c("Стороны признают юридическую силу документов, переданных по электронной почте, с последующим обменом оригиналами."),
    _c("Уступка прав по Договору третьим лицам без письменного согласия другой Стороны не допускается."),
    _c("Стороны обязуются сохранять конфиденциальность в отношении сведений, ставших известными в ходе исполнения Договора."),
    _c("Ни одна из Сторон не вправе использовать товарные знаки другой Стороны без предварительного письменного согласия."),
    lambda d: [V.T("Расторжение Договора в одностороннем порядке допускается при существенном нарушении его условий другой Стороной. Срок направления уведомления — ")]
              + d.val("TERM") + [V.T(".")],
    _c("Все приложения к Договору являются его неотъемлемой частью."),
    _c("Стороны подтверждают, что лица, подписавшие Договор, обладают всеми необходимыми полномочиями."),
]


# --------------------------------------------------------------------- сложная структура
# ГРУППА "complex" (задача 4). Существует НЕ для метрик новых видов данных, а
# чтобы потери чтения были измеримы и видны числом: известно, что программа на
# этих конструкциях теряет текст. Поэтому эталон здесь честный — он описывает
# то, что в документе ВИДНО, а не то, что программа сумеет достать.
#
# Отдельно про `tracked_del`. Удалённый правкой текст в документе НЕ виден, и
# координат не получает (corpus_lib.WRAPS_INVISIBLE). Он намеренно похож на
# сумму: извлекатель, который читает <w:delText> наравне с <w:t>, породит
# величину, которой в документе нет. Такой фантом эталон обязан ловить как
# ложное срабатывание, а не как пропуск.
COMPLEX_POOL = ["tracked_ins", "tracked_del", "field_simple", "field_complex",
                "smart_tag", "sdt_form", "header_footer", "huge_paragraphs"]

# Строка-фантом объявлена в реестре форм (values.DEL_GHOST): требование к ней —
# «реестр не должен уметь её породить» — это свойство реестра, там ему и место.
_DEL_GHOST = V.DEL_GHOST


def complex_block(d):
    """Раздел, оформленный средствами редактора. Только .docx группы complex."""
    L = []
    if d.group != "complex":
        return L
    tr = set(d.struct_tricks)
    L.append(para([]))
    L.append(para([d.t("11. УСЛОВИЯ, ОФОРМЛЕННЫЕ СРЕДСТВАМИ РЕДАКТОРА")], style="bold"))
    k = 0
    if "tracked_ins" in tr:
        k += 1
        L.append(para([d.t("11.%d. Цена с учётом протокола разногласий: " % k)]
                      + d.P(d.val("MONEY"), wrap="ins") + [d.t(".")]))
    if "tracked_del" in tr:
        k += 1
        L.append(para([d.t("11.%d. Прежняя редакция пункта: " % k),
                       chunk(_DEL_GHOST, wrap="del"),
                       d.t("утратила силу.")]))
    if "field_simple" in tr:
        k += 1
        L.append(para([d.t("11.%d. Ставка (поле Word): " % k)]
                      + d.P(d.val("PERCENT"), wrap="fld") + [d.t(".")]))
    if "field_complex" in tr:
        k += 1
        L.append(para([d.t("11.%d. Срок (составное поле): " % k)]
                      + d.P(d.val("TERM"), wrap="fldcomplex") + [d.t(".")]))
    if "smart_tag" in tr:
        k += 1
        L.append(para([d.t("11.%d. Ответственный (умный тег): " % k),
                       d.E(DT.make_person(d.rnd)["nom"], "PER", "adversarial",
                           trick="in_smart_tag", wrap="smarttag",
                           note="ФИО внутри w:smartTag"),
                       d.t(".")]))
    if "sdt_form" in tr:
        k += 1
        L.append(para([d.t("11.%d. Срок (элемент формы): " % k)]
                      + d.P(d.val("TERM"), wrap="sdt") + [d.t(".")]))
    return L


def huge_tail(d, n_para=2100):
    """Хвост на 2000+ абзацев. Величины ставятся редко и через реестр форм:
    задача хвоста — объём, а не разнообразие."""
    L = [para([]), para([d.t("12. РЕГЛАМЕНТ ВЗАИМОДЕЙСТВИЯ (РАСШИРЕННЫЙ)")], style="bold")]
    for i in range(n_para):
        if i % 50 == 49:
            L.append(para([d.t("12.%d. Контрольная величина: " % (i + 1))]
                          + d.P(d.val("MONEY")) + [d.t(".")]))
        else:
            L.append(para([d.t("12.%d. %s" % (i + 1, NOISE_PLAIN[i % len(NOISE_PLAIN)]))]))
    return L


# Плоский текст для объёмного хвоста: клаузы NOISE — функции, а звать их 2100
# раз значило бы утопить корпус в величинах из одного и того же места.
NOISE_PLAIN = [
    "Стороны действуют добросовестно и разумно.",
    "Переписка ведётся по адресам, указанным в реквизитах.",
    "Стороны уведомляют друг друга об изменении реквизитов.",
    "Настоящий пункт не изменяет распределения рисков между Сторонами.",
    "Приложения оформляются в том же порядке, что и Договор.",
    "Стороны вправе привлекать третьих лиц с письменного согласия.",
    "Документы направляются способом, позволяющим подтвердить отправку.",
]


# --------------------------------------------------------------------- сборка
def factory_block(d, doc_no):
    """Блок фабричных типов (ЭТАП TYPE-FACTORY-2).

    Выбор примера и негатива — от СКВОЗНОГО номера документа (doc_no), а не от
    d.rnd: блок не трогает генератор случайности документа, поэтому все
    остальные розыгрыши (реквизиты, адреса, шум) остаются теми же, что были
    до фабрики. Каждый документ несёт по одному примеру и одному негативу
    каждого фабричного типа; за корпус перебираются все примеры и все виды
    негативов (курсоры смещены и номером документа, и номером типа).
    """
    if not FACTORY_TYPES:
        return []
    out = [para([d.t("6. ОСОБЫЕ УСЛОВИЯ ОТДЕЛЬНЫХ ВИДОВ ДАННЫХ")], style="bold")]
    n = 0
    # d.ctype хранит ПОЛНОЕ название договора (D получает cname), поэтому
    # короткий ключ типа договора берём из doc_id: «loan_0001»/«cx_loan_0023».
    short_ctype = re.match(r"(?:cx_)?([a-z]+)_", d.doc_id).group(1)
    for k, ft in enumerate(FACTORY_TYPES):
        if ft["only_ctypes"] and short_ctype not in ft["only_ctypes"]:
            continue
        ex = ft["examples"][(doc_no + k) % len(ft["examples"])]
        text, form, axes = (ex["text"], ex.get("form"), ex.get("axes")) \
            if isinstance(ex, dict) else (ex, None, None)
        n += 1
        out.append(para([d.t("6.%d. %s" % (n, ft["intro"]))]
                        + [d.E(text, ft["type"], "canonical", form=form,
                               axes=axes, note="фабричный тип: пример из "
                                               "файла-описания владельца")]
                        + [d.t(ft["outro"])]))
        neg = ft["negatives"][(doc_no + k) % len(ft["negatives"])]
        n += 1
        out.append(para([d.t("6.%d. %s" % (n, ft["neg_intro"]))]
                        + [d.N(neg["text"], neg["why"], type_=ft["type"],
                               form=neg.get("form"), axes=neg.get("axes"),
                               kind=neg.get("kind"))]
                        + [d.t(".")]))
    out.append(para([]))
    return out if n else []


def build_doc(idx, ctype, cname, fmt, flags, seed, parties_kind, long_doc,
              group="simple", struct_tricks=(), form_start=0, prefix=""):
    doc_id = "%s%s_%04d" % (prefix, ctype, idx)
    d = D(doc_id, fmt, cname, seed, flags, group=group, form_start=form_start,
          struct_tricks=struct_tricks)
    rnd = d.rnd
    r1, r2 = ROLES[ctype]

    d.parties = "-".join(parties_kind)
    mk = {"LE": mk_le, "IP": mk_ip, "NP": mk_np}
    p1 = mk[parties_kind[0]](d)
    p2 = mk[parties_kind[1]](d)

    # ------- шапка
    if has(d, "caps_style_lower") and fmt == "docx":
        d.body.append(para([d.t(TITLES[ctype].lower())], style="capsstyle"))
    else:
        d.body.append(para([d.t(TITLES[ctype])], style="title"))
    d.body.append(para([d.t("№ ")] + d.P(d.val("DOCNUM")), style="title"))
    d.body.append(para([]))
    city = rnd.choice(DT.CITIES)[1]
    # Дата документа — тоже типизированный негатив с осью записи. Форма с
    # кавычками («24» июля 2007 г.) — одно из значений оси, а не единственная
    # запись, как было до этого этапа.
    dl = [d.I(city, "город без улицы/дома в шапке — серая зона: не пропуск и не ложное "
                    "срабатывание"), d.t("     ")] + d.P(d.val("DATE"))
    d.body.append(para(dl))
    d.body.append(para([]))

    # ------- преамбула
    d.body.append(preamble(d, p1, p2, r1, r2))
    d.body.append(para([]))

    # ЭТАП NEG — три блока негативов раздаются в ТРИ РАЗНЫХ МЕСТА документа
    # (предмет, место исполнения, прочие условия). Свалить их одной кучей в
    # раздел 2 следом за негативами A3 значило бы повторить ровно тот дефект
    # набора, ради которого этап и делается: один вид случая — одна позиция.
    neg_b1, neg_b2, neg_b3 = negatives_neg(d)

    # ------- предмет
    d.body.append(para([d.t("1. ПРЕДМЕТ ДОГОВОРА")], style="bold"))
    for i, c in enumerate(CLAUSES[ctype], start=1):
        d.body.append(para([d.t("1.%d. " % i)] + d.P(c(d))))
    for j, extra in enumerate(neg_b1, start=len(CLAUSES[ctype]) + 1):
        d.body.append(para([d.t("1.%d. " % j)] + extra["chunks"]))
    d.body.append(para([]))

    # ------- цена, налог, срок, транши (НОВЫЕ ВИДЫ ДАННЫХ) + негативы
    d.body.append(para([d.t("2. ЦЕНА И ПОРЯДОК РАСЧЁТОВ")], style="bold"))
    d.body.append(para([d.t("2.1. Общая цена Договора: ")] + d.P(d.val("MONEY")) + [d.t(".")]))
    d.body.append(para([d.t("2.2. Налог: ")] + d.P(d.val("PERCENT")) + [d.t(".")]))
    d.body.append(para([d.t("2.3. Срок оплаты — ")] + d.P(d.val("TERM")) + [d.t(".")]))
    n = 4
    if ctype in ("loan", "cession"):
        # Транши порождаются только там, где они вообще встречаются в жизни —
        # в займе и цессии. Раздать их всем девяти типам договоров значило бы
        # накрутить вхождения ценой правдоподобия, а вид данных и без того
        # слабый (см. шапку values.py).
        d.body.append(para([d.t("2.%d. Порядок выборки: " % n)] + d.P(d.val("TRANCHE"))
                           + [d.t(" определяется приложением к Договору.")]))
        n += 1
    d.body.append(para([d.t("2.%d. " % n)] + negatives_clause(d)["chunks"]))
    # ЭТАП A3 — негативы 13 типов, у которых их не было вовсе. Отдельными
    # абзацами, а не хвостом предыдущего: клауза негативов уже длиной в экран,
    # и дописывать в неё значило бы сделать нечитаемым и её, и находки.
    for extra in negatives_a3(d) + negatives_a7(d):
        if extra.get("t") == "tbl":
            d.body.append(extra)
        else:
            n += 1
            d.body.append(para([d.t("2.%d. " % n)] + extra["chunks"]))
    d.body.append(para([]))

    # ------- адрес места исполнения (ПДн в тексте, не в реквизитах)
    d.body.append(para([d.t("3. МЕСТО И СРОКИ ИСПОЛНЕНИЯ")], style="bold"))
    d.body.append(para([d.t("3.1. Место исполнения обязательств: "), ADDR(d), d.t(".")]))
    if p2["kind"] != "LE":
        d.body.append(para([d.t("3.2. Уведомления направляются "),
                            d.E(p2["person"]["dat"], "PER",
                                "ugly" if has(d, "oblique_names") else "canonical",
                                note="ФИО в дательном падеже"),
                            d.t(" по телефону "), PHONE(d), d.t(".")]))
    else:
        d.body.append(para([d.t("3.2. Контактное лицо со стороны %s — " % r2),
                            d.E(p2["person"]["gen"], "PER", "ugly",
                                note="ФИО в родительном падеже"),
                            d.t(", тел. "), PHONE(d), d.t(".")]))
    d.body.append(para([d.t("3.3. Срок исполнения обязательств — ")]
                       + d.P(d.val("TERM")) + [d.t(".")]))
    for j, extra in enumerate(neg_b2, start=4):
        d.body.append(para([d.t("3.%d. " % j)] + extra["chunks"]))
    d.body.append(para([]))

    # ------- шум
    n_noise = 12 if long_doc else 5
    d.body.append(para([d.t("4. ПРОЧИЕ УСЛОВИЯ")], style="bold"))
    for i in range(n_noise):
        d.body.append(para([d.t("4.%d. " % (i + 1))] + d.P(NOISE[i % len(NOISE)](d))))
    for j, extra in enumerate(neg_b3, start=n_noise + 1):
        d.body.append(para([d.t("4.%d. " % j)] + extra["chunks"]))
    if long_doc:
        d.body.append(para([]))
        d.body.append(para([d.t("5. ЗАВЕРЕНИЯ ОБ ОБСТОЯТЕЛЬСТВАХ")], style="bold"))
        for i in range(8):
            d.body.append(para([d.t("5.%d. " % (i + 1))]
                               + d.P(NOISE[(i + 5) % len(NOISE)](d))))
        # ПДн в глубине шумного окружения
        d.body.append(para([d.t("5.9. Сторона-1 подтверждает, что уполномоченным лицом "
                                "по вопросам исполнения является "),
                            d.E(p1["person"]["nom"], "PER", "canonical"),
                            d.t(", тел. "), PHONE(d), d.t(", адрес для корреспонденции: "),
                            ADDR(d), d.t(".")]))
    d.body.append(para([]))

    # ------- состязательный раздел (ПДн старого набора — по флагам)
    d.body.extend(adversarial_block(d, p1, p2))
    d.body.append(para([]))

    # ------- состязательные записи НОВЫХ видов данных (задача 3) — ВЕЗДЕ
    # Ключ раскладки приёмов — СКВОЗНОЙ номер документа (form_start), а не
    # номер внутри своего типа договора: иначе поставки и подряды получали бы
    # один и тот же приём на одном и том же виде данных.
    d.body.extend(adversarial_values_block(d, form_start))
    d.body.append(para([]))

    # ------- сложная структура (только группа complex)
    d.body.extend(complex_block(d))
    if "huge_paragraphs" in d.struct_tricks:
        d.body.extend(huge_tail(d))

    # ------- фабричные типы (TYPE-FACTORY-2) — свой блок, БЕЗ d.rnd
    d.body.extend(factory_block(d, form_start))

    # ------- реквизиты
    d.body.append(para([d.t("РЕКВИЗИТЫ И ПОДПИСИ СТОРОН")], style="bold"))
    d.body.append(para([]))
    if fmt == "docx":
        c1 = req_lines(d, p1, r1)
        c2 = req_lines(d, p2, r2)
        if has(d, "bare_cell"):
            c1.insert(1, para([d.E(p1["person"]["nom"], "PER", "adversarial",
                                   trick="bare_cell_no_sentence",
                                   note="голая строка ПДн в ячейке, без предложения вокруг")]))
            c2.insert(1, para([d.E(make_address(rnd, "no_marker"), "ADDRESS", "adversarial",
                                   trick="bare_cell_no_sentence",
                                   note="голый адрес в ячейке")]))
        if has(d, "bold_split"):
            c1.append(para([d.t("Согласовано: "),
                            d.E(p1["person"]["nom"], "PER", "adversarial",
                                trick="bold_split_runs", bs=True,
                                note="слово разорвано форматированием (половина букв жирная)")]))
        if has(d, "nested_table"):
            inner = table([[cell([para([d.t("Банк")])]), cell([para([d.t("Счёт")])])],
                           [cell([para([d.t(p2["bank"])])]),
                            cell([para([d.E(p2["acc"], "ACCOUNT", "adversarial",
                                            trick="nested_table",
                                            note="счёт во вложенной таблице")])])],
                           [cell([para([d.t("Контакт")])]),
                            cell([para([d.E(p2["person"]["short"], "PER", "adversarial",
                                            trick="nested_table",
                                            note="ФИО во вложенной таблице")])])]])
            c2.append(inner)
            c2.append(para([]))   # OOXML: ячейка обязана заканчиваться абзацем
        d.body.append(table([[cell(c1), cell(c2)]]))
        if has(d, "split_cell"):
            inn = p1["inn"]
            eid = d.nid()
            nm = p2["person"]["nom"]
            k = nm.find(" ")
            eid2 = d.nid()
            d.body.append(para([]))
            d.body.append(table([
                [cell([para([d.t("ИНН: "),
                             d.E(inn[:4], "INN", "adversarial", eid=eid,
                                 trick="cell_boundary_split",
                                 note="первые цифры ИНН в одной ячейке, остальные — в соседней")])]),
                 cell([para([d.E(inn[4:], "INN", "adversarial", eid=eid,
                                 trick="cell_boundary_split")])])],
                [cell([para([d.E(nm[:k], "PER", "adversarial", eid=eid2,
                                 trick="cell_boundary_split",
                                 note="ФИО разорвано границей ячейки")])]),
                 cell([para([d.E(nm[k + 1:], "PER", "adversarial", eid=eid2,
                                 trick="cell_boundary_split")])])]]))
    else:
        for p, role in ((p1, r1), (p2, r2)):
            d.body.extend(req_lines(d, p, role))
            d.body.append(para([]))

    # ------- приложение
    if has(d, "appendix_pii"):
        d.body.append(para([]))
        d.body.append(para([d.N("ПРИЛОЖЕНИЕ № 1", "номер приложения — не ПДн"),
                            d.t(" к Договору")], style="bold"))
        d.body.append(para([d.t("Адрес склада грузополучателя: "), ADDR(d), d.t(".")]))
        d.body.append(para([d.t("Кладовщик: "),
                            d.E(DT.make_person(rnd)["nom"], "PER", "ugly",
                                note="ПДн в приложении к договору")]))
        d.body.append(para([d.t("Тел. склада: "), PHONE(d)]))

    # ------- колонтитулы / сноски / текстбокс (только docx)
    if fmt == "docx":
        if has(d, "hdr_pii"):
            d.header.append(para([
                d.t("%s · " % (p1["name"] if p1["kind"] == "LE" else "ИП " + p1["person"]["short"])),
                d.E(phone_str(rnd, "canonical"), "PHONE", "adversarial",
                    trick="in_header", note="телефон в колонтитуле (верхнем)"),
                d.t(" · "),
                d.E(email_for(rnd, p1["person"]), "EMAIL", "adversarial",
                    trick="in_header", note="e-mail в колонтитуле")]))
        if has(d, "ftr_pii"):
            d.footer.append(para([
                d.t("Исполнитель документа: "),
                d.E(DT.make_person(rnd)["short"], "PER", "adversarial",
                    trick="in_footer", note="ФИО в нижнем колонтитуле"),
                d.t(", тел. "),
                d.E(phone_str(rnd, "tight8"), "PHONE", "adversarial",
                    trick="in_footer", note="телефон в нижнем колонтитуле")]))
        if has(d, "footnote_pii"):
            fn = [d.t("Реквизиты для возврата: "),
                  d.E(make_address(rnd, "canonical"), "ADDRESS", "adversarial",
                      trick="in_footnote", note="адрес в сноске"),
                  d.t(", ИНН "),
                  d.E(DT.inn10(rnd), "INN", "adversarial",
                      trick="in_footnote", note="ИНН в сноске"),
                  d.t(", контакт: "),
                  d.E(DT.make_person(rnd)["nom"], "PER", "adversarial",
                      trick="in_footnote", note="ФИО в сноске")]
            d.body.insert(6, para([d.t("Стороны согласовали порядок возврата товара.")], footnote=fn))
        if has(d, "textbox_pii"):
            d.body.append(textbox([
                d.t("ВНИМАНИЕ! Бухгалтерия: "),
                d.E(DT.make_person(rnd)["nom"], "PER", "adversarial",
                    trick="in_textbox", note="ФИО в текстбоксе/надписи"),
                d.t(", "),
                d.E(phone_str(rnd, "ext"), "PHONE", "adversarial",
                    trick="in_textbox", note="телефон в текстбоксе")]))

    # ------- подписи
    d.body.append(para([]))
    d.body.append(para([d.t("Подписи сторон:")]))
    d.body.append(para([d.t("%s: _______________ /" % r1),
                        d.E(p1["person"]["short"] if p1["kind"] != "NP" else p1["person"]["nom"],
                            "PER", "ugly", note="подпись: фамилия+инициалы"),
                        d.t("/")]))
    d.body.append(para([d.t("%s: _______________ /" % r2),
                        d.E(p2["person"]["short"] if p2["kind"] != "NP" else p2["person"]["nom"],
                            "PER", "ugly", note="подпись: фамилия+инициалы"),
                        d.t("/")]))
    return d


# --------------------------------------------------------------------- планировщик
def plan():
    docs = []
    idx = 0
    kinds = [("LE", "LE"), ("LE", "IP"), ("LE", "NP"), ("IP", "IP"), ("IP", "NP"), ("NP", "NP")]
    for ctype, cname, n in CONTRACTS:
        for j in range(n):
            fmt = "docx" if (idx % 2 == 1) else "txt"
            pk = kinds[idx % len(kinds)]
            if ctype == "labor":
                pk = ("LE", "NP") if j % 3 else ("IP", "NP")
            long_doc = (idx % 7 == 0)
            docs.append(dict(idx=idx, ctype=ctype, cname=cname, fmt=fmt, kinds=pk,
                             long=long_doc, doc_no=j + 1))
            idx += 1
    # ---- раскладка приёмов
    docx_i = [i for i, x in enumerate(docs) if x["fmt"] == "docx"]
    for k, i in enumerate(docx_i):
        docs[i].setdefault("flags", set())
        docs[i]["flags"].add(DOCX_POOL[k % len(DOCX_POOL)])
        docs[i]["flags"].add(DOCX_POOL[(k + 4) % len(DOCX_POOL)])
    for i, x in enumerate(docs):
        x.setdefault("flags", set())
        if i < 18:
            continue                      # чистая каноника — baseline
        x["flags"].add(UGLY_POOL[i % len(UGLY_POOL)])
        x["flags"].add(UGLY_POOL[(i + 7) % len(UGLY_POOL)])
        x["flags"].add(UGLY_POOL[(i + 13) % len(UGLY_POOL)])
        if i >= 40:
            x["flags"].add(ADV_POOL[i % len(ADV_POOL)])
            x["flags"].add(ADV_POOL[(i + 5) % len(ADV_POOL)])
    for i, x in enumerate(docs):
        if i < 18:
            x["flags"] = set()
    return docs


def plan_complex():
    """Группа «сложная структура»: только .docx, приёмы раздаются так, чтобы
    каждый из COMPLEX_POOL встретился, а `huge_paragraphs` — ровно дважды
    (документ на 2000+ абзацев дорог, но без него класс не покрыт)."""
    docs = []
    kinds = [("LE", "LE"), ("LE", "IP"), ("LE", "NP"), ("IP", "NP"), ("NP", "NP")]
    base = [c[0] for c in CONTRACTS]
    for j in range(30):
        ctype = base[j % len(base)]
        cname = dict((c[0], c[1]) for c in CONTRACTS)[ctype]
        tricks = {COMPLEX_POOL[j % len(COMPLEX_POOL)],
                  COMPLEX_POOL[(j + 3) % len(COMPLEX_POOL)]}
        tricks.discard("huge_paragraphs")
        if j in (7, 21):
            tricks.add("huge_paragraphs")
        flags = set()
        if "header_footer" in tricks:
            flags |= {"hdr_pii", "ftr_pii"}
        docs.append(dict(idx=j, ctype=ctype, cname=cname, fmt="docx",
                         kinds=kinds[j % len(kinds)], long=False, doc_no=j + 1,
                         flags=flags, group="complex",
                         struct_tricks=sorted(tricks), prefix="cx_"))
    return docs


def main():
    root = ROOT
    p_gold = os.path.join(root, "gold_v2.json")
    if os.path.exists(p_gold):
        os.remove(p_gold)
    docs = plan()
    for x in docs:
        x.setdefault("group", "simple")
        x.setdefault("struct_tricks", [])
        x.setdefault("prefix", "")
    docs += plan_complex()

    entries = []
    stats = {}
    forms = {}
    axis_hits = {}
    tricks = {}
    for x in docs:
        d = build_doc(x["doc_no"], x["ctype"], x["cname"], x["fmt"], x["flags"],
                      1000 + x["idx"], x["kinds"], x["long"],
                      group=x["group"], struct_tricks=x["struct_tricks"],
                      # СМЕЩЕНИЕ по реестру форм своё у каждого документа —
                      # иначе в корпус попали бы только первые формы каждого вида.
                      form_start=x["idx"], prefix=x["prefix"])
        m = d.model()
        render(m, root)
        e = gold_entry(m)
        entries.append(e)
        # Вхождение вида данных — это и величина, и ТИПИЗИРОВАННЫЙ НЕГАТИВ
        # (пустое место под сумму, «Без НДС», номер договора, дата). Считать
        # только величины значило бы потерять половину покрытия осей.
        for en in list(e["entities"]) + list(e["negatives"]):
            t = en.get("type")
            if not t:
                continue
            stats[t] = stats.get(t, 0) + 1
            if en.get("form"):
                forms.setdefault(t, set()).add(en["form"])
            for name, val in (en.get("axes") or {}).items():
                axis_hits.setdefault(t, {}).setdefault(name, {})
                axis_hits[t][name][val] = axis_hits[t][name].get(val, 0) + 1
            if en.get("trick") and t in V.NEW_TYPES:
                tricks[en["trick"]] = tricks.get(en["trick"], 0) + 1
    n = update_gold(root, entries)

    n_simple = sum(1 for e in entries if e["structure_group"] == "simple")
    n_complex = sum(1 for e in entries if e["structure_group"] == "complex")
    print("КОРПУС V2: документов %d (простая структура %d, сложная %d) | gold %d"
          % (len(entries), n_simple, n_complex, n))
    print("сущностей:", sum(len(e["entities"]) for e in entries),
          "| негативов:", sum(len(e["negatives"]) for e in entries))
    print("--- вхождения по видам данных ---")
    for k in sorted(stats, key=lambda z: -stats[z]):
        nf = len(forms.get(k, ()))
        print("  %-10s %5d %s" % (k, stats[k],
                                  ("| разных форм записи: %d" % nf) if nf else ""))
    print("--- покрытие осей (вхождений на значение) ---")
    for k in sorted(V.AXES):
        total = stats.get(k, 0)
        possible = 1
        for vals in V.AXES[k].values():
            possible *= len(vals)
        print("  %s: %d вхождений, %d разных форм (комбинаций осей), "
              "теоретический предел %d" % (k, total, len(forms.get(k, ())), possible))
        for name in V.AXIS_ORDER[k]:
            hits = axis_hits.get(k, {}).get(name, {})
            applies = sum(hits.values())
            miss = [v for v in V.AXES[k][name] if v not in hits]
            print("    %-24s применима к %4d | %s%s"
                  % (name, applies,
                     ", ".join("%s=%d" % (v, hits[v]) for v in V.AXES[k][name] if v in hits),
                     ("  НЕ ВСТРЕТИЛИСЬ: " + ", ".join(miss)) if miss else ""))
    print("--- состязательные приёмы над новыми видами ---")
    for t in sorted(tricks, key=lambda z: -tricks[z]):
        print("  %-16s %4d" % (t, tricks[t]))


if __name__ == "__main__":
    main()
