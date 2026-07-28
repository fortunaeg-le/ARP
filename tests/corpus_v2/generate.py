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
import sys

import data as DT
import values as V
from corpus_lib import (NBSP, NNBSP, ZWSP, ZWJ, SHY, WJ, CYR2LAT, DIGIT2CYR,
                        chunk, ent, neg as cl_neg, para, table, cell, textbox,
                        render, update_gold, serialize, gold_entry)

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
             "org_name_is_person", "appendix_pii", "invalid_checksum"]
ADV_POOL = ["homoglyph", "invisible", "case_lower", "case_upper", "case_mixed",
            "digit_spaces", "linebreak_entity", "dense_line", "addr_glued",
            "same_name_5_cases", "email_homoglyph", "zw_in_name"]


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

    def N(self, s, why, type_=None, form=None, axes=None, trick=None, nid=None):
        n = cl_neg(why, type_, form, axes)
        if trick:
            n["trick"] = trick
        if nid:
            # Общий id — негатив, разорванный границей ячейки, остаётся ОДНИМ
            # вхождением (см. corpus_lib._Out.emit).
            n["id"] = nid
        return chunk(s, neg=n)

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
                _, s, type_, why, form, axes = p
                out.append(chunk(s, neg=cl_neg(why, type_, form, axes), wrap=wrap))
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
    return dict(kind="NP", person=person, inn=DT.inn12(rnd, not bad),
                snils=DT.snils(rnd, not bad), passport=DT.passport(rnd),
                pass_dept="%03d-%03d" % (rnd.randint(1, 999), rnd.randint(1, 999)),
                pass_org=rnd.choice(['ОВД «Тропарёво» г. Москвы', 'ГУ МВД России по г. Москве',
                                     'ОУФМС России по Тверской области в Заволжском р-не',
                                     'ТП № 2 ОУФМС России по Московской обл.']),
                pass_date="%02d.%02d.%d" % (rnd.randint(1, 28), rnd.randint(1, 12),
                                            rnd.randint(2002, 2020)),
                bank=bank, bik=bik, corr=corr,
                acc=DT.account(rnd, bik, prefix="40817810", valid=not bad),
                birth="%02d.%02d.%d" % (rnd.randint(1, 28), rnd.randint(1, 12),
                                        rnd.randint(1955, 2000)),
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
                   d.t("паспорт "),
                   d.E(p["passport"], "PASSPORT", "ugly",
                       note="серия+номер через пробел"),
                   d.t(", выдан "), d.t(p["pass_org"]), d.t(" "),
                   d.I(p["pass_date"], "дата выдачи паспорта — серая зона (не ПДн-сущность в нашей схеме)"),
                   d.t(", код подразделения "),
                   d.E(p["pass_dept"], "PASSPORT", "ugly", note="код подразделения"),
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
        L.append(para([d.t("Дата рождения: "),
                       d.E(p["birth"], "BIRTHDATE", "canonical")]))
        L.append(para([d.t("Адрес регистрации: "), ADDR(d)]))
        L.append(para([d.t("Паспорт "), d.E(p["passport"], "PASSPORT", "canonical"),
                       d.t(", выдан " + p["pass_org"] + " " + p["pass_date"]),
                       d.t(", код подразделения "),
                       d.E(p["pass_dept"], "PASSPORT", "ugly", note="код подразделения")]))
        L.append(para([d.t("СНИЛС "), d.E(p["snils"], "SNILS", "canonical",
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
        _, s, type_, why, form, axes = part
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
            return [d.N(s[:i], why, type_, form, axes, trick=trick, nid=eid),
                    d.N(s[i:], why, type_, form, axes, trick=trick, nid=eid)]
        return [d.E(s[:i], type_, "adversarial", trick=trick, note=note, eid=eid,
                    form=form, axes=axes),
                d.E(s[i:], type_, "adversarial", trick=trick, eid=eid,
                    form=form, axes=axes)]
    else:
        raise ValueError(trick)

    if kind == "n":
        return [d.N(s, why, type_, form, axes, trick=trick)]
    return [d.E(s, type_, "adversarial", trick=trick, note=note, form=form,
                bs=bs, axes=axes)]


# Приёмы, выполнимые в .txt (там нет ни ранов, ни ячеек).
TXT_TRICKS = ["homoglyph", "invisible", "digit_spaces", "linebreak"]
DOCX_TRICKS = TXT_TRICKS + ["cell_split"]

TRICK_NOTE = {
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
          # В старом корпусе сумма стояла здесь НЕГАТИВОМ («сумма — не ПДн»).
          # В V2 сумма — полноценный вид данных, поэтому она размечена.
          d.t(". Цена — ")] + d.P(d.val("MONEY")) + [d.t(".")]
    return para(ch)


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

    # ------- предмет
    d.body.append(para([d.t("1. ПРЕДМЕТ ДОГОВОРА")], style="bold"))
    for i, c in enumerate(CLAUSES[ctype], start=1):
        d.body.append(para([d.t("1.%d. " % i)] + d.P(c(d))))
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
    d.body.append(para([]))

    # ------- шум
    n_noise = 12 if long_doc else 5
    d.body.append(para([d.t("4. ПРОЧИЕ УСЛОВИЯ")], style="bold"))
    for i in range(n_noise):
        d.body.append(para([d.t("4.%d. " % (i + 1))] + d.P(NOISE[i % len(NOISE)](d))))
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
