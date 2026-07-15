# -*- coding: utf-8 -*-
"""
generate.py — базовый корпус (108 документов).

Разметка ставится НА ЧАНК В МОМЕНТ ГЕНЕРАЦИИ (метод D.E). Координаты никогда
не ищутся в готовом тексте — они вычисляются сериализатором из модели.
"""
import os
import random
import sys

import data as DT
from corpus_lib import (NBSP, NNBSP, ZWSP, ZWJ, SHY, WJ, CYR2LAT, DIGIT2CYR,
                        chunk, ent, para, table, cell, textbox,
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
    def __init__(self, doc_id, fmt, ctype, seed, flags):
        self.doc_id, self.format, self.ctype = doc_id, fmt, ctype
        self.rnd = random.Random(seed)
        self.flags = flags
        self._n = 0
        self.header, self.footer, self.body = [], [], []
        self.parties = ""

    def nid(self):
        self._n += 1
        return "%s-e%d" % (self.doc_id, self._n)

    def t(self, s):
        return chunk(s)

    def E(self, s, type_, cat="canonical", trick=None, note=None, checksum=None,
          eid=None, bs=False):
        return chunk(s, ent=ent(type_, cat, eid or self.nid(), trick, note, checksum),
                     bold_split=bs)

    def N(self, s, why):
        return chunk(s, neg={"why": why})

    def I(self, s, why):
        return chunk(s, ignore={"why": why})

    def model(self):
        m = {"doc_id": self.doc_id, "format": self.format, "source": "base",
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
    ch += [d.t(", с другой стороны, совместно именуемые «Стороны», заключили настоящий "),
           d.N("Договор № %d/%d-%s" % (rnd.randint(1, 99), rnd.choice([2023, 2024, 2025]),
                                       rnd.choice("ПАСКД")), "номер договора — не ПДн"),
           d.t(" (далее — Договор) о нижеследующем:")]
    return para(ch)


# --------------------------------------------------------------------- реквизиты
def money(rnd):
    return "%s %03d,00 руб." % (rnd.randint(100, 990), rnd.randint(0, 999))


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
          d.t(". Цена — "), d.N(money(rnd), "сумма — не ПДн"), d.t(".")]
    return para(ch)


CLAUSES = {
    "supply": [
        "Поставщик обязуется передать в собственность Покупателя товар, а Покупатель — принять товар и уплатить за него цену в порядке и сроки, предусмотренные Договором.",
        "Наименование, ассортимент, количество и цена товара определяются в спецификациях, являющихся неотъемлемой частью Договора.",
        "Поставка осуществляется партиями на основании заявок Покупателя, направляемых не позднее чем за 5 (пять) рабочих дней до предполагаемой даты отгрузки.",
        "Право собственности на товар переходит к Покупателю с момента подписания товарной накладной уполномоченными представителями Сторон.",
        "Качество товара должно соответствовать требованиям технических регламентов и подтверждаться сертификатами соответствия.",
    ],
    "services": [
        "Исполнитель обязуется по заданию Заказчика оказать услуги, а Заказчик обязуется оплатить эти услуги.",
        "Перечень, объём и сроки оказания услуг согласовываются Сторонами в заданиях, оформляемых в письменной форме.",
        "Услуги считаются оказанными после подписания Сторонами акта сдачи-приёмки оказанных услуг.",
        "Заказчик вправе в любое время проверять ход и качество оказываемых услуг, не вмешиваясь в деятельность Исполнителя.",
    ],
    "lease": [
        "Арендодатель обязуется передать Арендатору за плату во временное владение и пользование нежилое помещение.",
        "Помещение передаётся по акту приёма-передачи в течение 3 (трёх) рабочих дней с даты подписания Договора.",
        "Арендная плата вносится ежемесячно не позднее 10-го числа текущего месяца.",
        "Арендатор обязан поддерживать помещение в исправном состоянии и производить текущий ремонт за свой счёт.",
    ],
    "works": [
        "Подрядчик обязуется выполнить по заданию Заказчика работы и сдать их результат, а Заказчик — принять результат работ и оплатить его.",
        "Работы выполняются иждивением Подрядчика — из его материалов, его силами и средствами.",
        "Сдача результата работ оформляется актом по форме КС-2 и справкой по форме КС-3.",
        "Гарантийный срок на результат работ составляет 24 (двадцать четыре) месяца с даты подписания акта.",
    ],
    "loan": [
        "Займодавец передаёт в собственность Заёмщику денежные средства, а Заёмщик обязуется возвратить сумму займа в срок и в порядке, установленные Договором.",
        "Сумма займа считается возвращённой в момент зачисления денежных средств на счёт Займодавца.",
        "За пользование займом Заёмщик уплачивает проценты по ставке 12 (двенадцать) процентов годовых.",
        "Досрочный возврат суммы займа допускается с письменного согласия Займодавца.",
    ],
    "cession": [
        "Цедент уступает, а Цессионарий принимает право требования к должнику, возникшее из договора поставки.",
        "Цедент обязан передать Цессионарию все документы, удостоверяющие уступаемое право требования.",
        "Уступка права требования является возмездной.",
        "Цедент отвечает за недействительность переданного требования, но не отвечает за неисполнение обязательства должником.",
    ],
    "agency": [
        "Агент обязуется за вознаграждение совершать по поручению Принципала юридические и иные действия от своего имени, но за счёт Принципала.",
        "Агент представляет Принципалу отчёт по мере исполнения поручения, но не реже одного раза в месяц.",
        "Агентское вознаграждение составляет 7 (семь) процентов от суммы заключённых сделок.",
        "Принципал обязан возместить Агенту расходы, понесённые при исполнении поручения.",
    ],
    "labor": [
        "Работник принимается на работу на должность инженера-технолога в производственный отдел.",
        "Работа по настоящему Договору является для Работника основным местом работы.",
        "Работнику устанавливается пятидневная рабочая неделя с двумя выходными днями.",
        "Работнику устанавливается должностной оклад и выплачивается заработная плата не реже чем каждые полмесяца.",
        "Работнику предоставляется ежегодный оплачиваемый отпуск продолжительностью 28 календарных дней.",
    ],
    "sale": [
        "Продавец обязуется передать в собственность Покупателя имущество, а Покупатель — принять его и уплатить цену.",
        "Продавец гарантирует, что имущество не заложено, не находится под арестом и свободно от прав третьих лиц.",
        "Право собственности переходит к Покупателю с момента государственной регистрации перехода права.",
        "Расчёты производятся через аккредитив, открываемый Покупателем.",
    ],
}

NOISE = [
    "Стороны несут ответственность за неисполнение или ненадлежащее исполнение обязательств в соответствии с законодательством Российской Федерации.",
    "За нарушение сроков оплаты начисляется неустойка в размере 0,1 % от суммы задолженности за каждый день просрочки, но не более 10 % от суммы обязательства.",
    "Стороны освобождаются от ответственности при наступлении обстоятельств непреодолимой силы, если они прямо повлияли на исполнение обязательств.",
    "Сторона, для которой создалась невозможность исполнения, обязана уведомить другую Сторону в течение 5 (пяти) рабочих дней с даты наступления таких обстоятельств.",
    "Все споры и разногласия решаются путём переговоров. Претензионный порядок обязателен, срок ответа на претензию — 15 календарных дней.",
    "Договор вступает в силу с момента подписания и действует до полного исполнения Сторонами принятых обязательств.",
    "Изменения и дополнения к Договору действительны при условии их совершения в письменной форме и подписания уполномоченными представителями Сторон.",
    "Договор составлен в двух экземплярах, имеющих равную юридическую силу, по одному для каждой из Сторон.",
    "Стороны признают юридическую силу документов, переданных по электронной почте, с последующим обменом оригиналами.",
    "Уступка прав по Договору третьим лицам без письменного согласия другой Стороны не допускается.",
    "Стороны обязуются сохранять конфиденциальность в отношении сведений, ставших известными в ходе исполнения Договора.",
    "Ни одна из Сторон не вправе использовать товарные знаки другой Стороны без предварительного письменного согласия.",
    "Расторжение Договора в одностороннем порядке допускается при существенном нарушении его условий другой Стороной.",
    "Все приложения к Договору являются его неотъемлемой частью.",
    "Стороны подтверждают, что лица, подписавшие Договор, обладают всеми необходимыми полномочиями.",
]


# --------------------------------------------------------------------- сборка
def build_doc(idx, ctype, cname, fmt, flags, seed, parties_kind, long_doc):
    doc_id = "%s_%04d" % (ctype, idx)
    d = D(doc_id, fmt, cname, seed, flags)
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
    d.body.append(para([d.N("№ %d/%d-%s" % (rnd.randint(1, 199), rnd.choice([2023, 2024, 2025]),
                                            rnd.choice("ПАСКД")), "номер договора — не ПДн")],
                       style="title"))
    d.body.append(para([]))
    city = rnd.choice(DT.CITIES)[1]
    dl = [d.I(city, "город без улицы/дома в шапке — серая зона: не пропуск и не ложное "
                    "срабатывание"), d.t("     "),
          d.N("«%02d» %s %d г." % (rnd.randint(1, 28),
                                   rnd.choice(["января", "марта", "мая", "июля", "сентября",
                                               "ноября"]), rnd.choice([2023, 2024, 2025])),
              "дата документа — не ПДн")]
    d.body.append(para(dl))
    d.body.append(para([]))

    # ------- преамбула
    d.body.append(preamble(d, p1, p2, r1, r2))
    d.body.append(para([]))

    # ------- предмет
    d.body.append(para([d.t("1. ПРЕДМЕТ ДОГОВОРА")], style="bold"))
    for i, c in enumerate(CLAUSES[ctype], start=1):
        d.body.append(para([d.t("1.%d. %s" % (i, c))]))
    d.body.append(para([]))

    # ------- цена и негативы
    d.body.append(para([d.t("2. ЦЕНА И ПОРЯДОК РАСЧЁТОВ")], style="bold"))
    d.body.append(para([d.t("2.1. Общая цена Договора составляет "),
                        d.N(money(rnd), "сумма — не ПДн"),
                        d.t(", в том числе НДС 20 %.")]))
    d.body.append(para([d.t("2.2. ")] + negatives_clause(d)["chunks"]))
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
    d.body.append(para([]))

    # ------- шум
    n_noise = 12 if long_doc else 5
    d.body.append(para([d.t("4. ПРОЧИЕ УСЛОВИЯ")], style="bold"))
    for i in range(n_noise):
        d.body.append(para([d.t("4.%d. %s" % (i + 1, NOISE[i % len(NOISE)]))]))
    if long_doc:
        d.body.append(para([]))
        d.body.append(para([d.t("5. ЗАВЕРЕНИЯ ОБ ОБСТОЯТЕЛЬСТВАХ")], style="bold"))
        for i in range(8):
            d.body.append(para([d.t("5.%d. %s" % (i + 1, NOISE[(i + 5) % len(NOISE)]))]))
        # ПДн в глубине шумного окружения
        d.body.append(para([d.t("5.9. Сторона-1 подтверждает, что уполномоченным лицом "
                                "по вопросам исполнения является "),
                            d.E(p1["person"]["nom"], "PER", "canonical"),
                            d.t(", тел. "), PHONE(d), d.t(", адрес для корреспонденции: "),
                            ADDR(d), d.t(".")]))
    d.body.append(para([]))

    # ------- состязательный раздел
    d.body.extend(adversarial_block(d, p1, p2))
    d.body.append(para([]))

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


def main():
    root = ROOT
    if os.path.exists(os.path.join(root, "gold.json")):
        os.remove(os.path.join(root, "gold.json"))
    docs = plan()
    entries = []
    stats = {}
    for x in docs:
        d = build_doc(x["doc_no"], x["ctype"], x["cname"], x["fmt"], x["flags"],
                      1000 + x["idx"], x["kinds"], x["long"])
        m = d.model()
        render(m, root)
        e = gold_entry(m)
        entries.append(e)
        ents = e["entities"]
        for en in ents:
            stats[en["type"]] = stats.get(en["type"], 0) + 1
    n = update_gold(root, entries)
    print("base docs:", len(entries), "| gold entries:", n)
    print("entities:", sum(len(e["entities"]) for e in entries),
          "| negatives:", sum(len(e["negatives"]) for e in entries))
    for k in sorted(stats, key=lambda z: -stats[z]):
        print("  %-10s %d" % (k, stats[k]))


if __name__ == "__main__":
    main()
