# -*- coding: utf-8 -*-
"""Встречается ли КЛАСС отрицательного примера в реальном договоре — числом.

Третий вопрос владельца по итогам первого замера. Печатается ТОЛЬКО КОЛИЧЕСТВО:
ни одной строки документа, ни одного значения. Реальный договор лежит вне
репозитория (`C:\\shifrator_real\\dog.docx`), в историю не попадает ничего.

Что считается по каждому из девяти массовых случаев — ровно тот признак, по
которому детектор и ошибается:

  BIK      9-значные прогоны, начатые «04», у которых СЛЕВА в 40 символах нет
           слова «БИК» (у типа нет ни якоря, ни контрольной суммы);
  KPP      то же для любых 9-значных прогонов и слова «КПП»;
  ACCOUNT  вхождения «счёт-фактура» в любой форме — это и есть якорь ACCOUNT в
           другом значении; плюс сколько из них имеют 20-значный прогон справа;
  INN      вхождения оборотов «не присвоен»/«регистрационный номер» рядом с
           якорем «ИНН» — формулировка, на которой якорь снимает контрольную
           сумму;
  PHONE    совпадения формы телефона, ВНУТРИ которых есть разделитель ячейки
           (склейка соседних ячеек строки, находка B3);
  PERCENT  процент рядом со словом технического параметра;
  TERM     «в течение …» в предложении со ссылкой на норму + оборот «исковой
           давности».

Запуск:
    venv/Scripts/python.exe experiments/stage_a3/real_probe.py [путь.docx]
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

DEFAULT = r"C:\shifrator_real\dog.docx"

from extractor import extract          # noqa: E402
from normalizer import normalize_for_detection  # noqa: E402

WIN = 40

RE_9 = re.compile(r"(?<!\d)\d(?:[  ]?\d){8}(?!\d)")
RE_9_04 = re.compile(r"(?<!\d)0[  ]?4(?:[  ]?\d){7}(?!\d)")
RE_20 = re.compile(r"(?<!\d)\d(?:[  ]?\d){19}(?!\d)")
RE_PHONE = re.compile(r"(?<!\d)(\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)")
RE_INVOICE = re.compile(r"(?i)сч[ёе]т\w*[-\s]*фактур\w*")
RE_INN_ABSENT = re.compile(r"(?i)инн[^\n]{0,40}?(?:не\s+присвоен|регистрационн\w+\s+номер)")
RE_TECH_PCT = re.compile(
    r"(?i)(?:влажност\w*|массов\w*\s+дол\w*|содержани\w*|погрешност\w*|износ\w*|"
    r"концентраци\w*)[^\n]{0,60}?\d[^\n]{0,6}?(?:%|процент\w*)")
RE_TERM_NORM = re.compile(
    r"(?i)в\s+течение\s[^\n.]{0,80}?(?:дн\w*|месяц\w*|год\w*|лет|час\w*)[^\n.]{0,80}?"
    r"(?:стать\w*|закон\w*|ГК\s+РФ|Кодекс\w*)")
RE_LIMITATION = re.compile(r"(?i)исков\w*\s+давност\w*")


def anchored(text, start, word_re):
    return bool(word_re.search(text[max(0, start - WIN):start]))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    if not os.path.exists(path):
        print("документ не найден: %s — замер не проводился" % path)
        return 1
    segments = extract(path).segments
    # Текст сегмента — то же представление, по которому работает детекция.
    raw = "\n".join(s.text for s in segments)
    text, _map = normalize_for_detection(raw)

    re_bik = re.compile(r"(?i)бик")
    re_kpp = re.compile(r"(?i)кпп")

    bik_all = list(RE_9_04.finditer(text))
    bik_bare = [m for m in bik_all if not anchored(text, m.start(), re_bik)]
    kpp_all = list(RE_9.finditer(text))
    kpp_bare = [m for m in kpp_all if not anchored(text, m.start(), re_kpp)]

    invoices = list(RE_INVOICE.finditer(text))
    inv_with_20 = sum(1 for m in invoices
                      if RE_20.search(text[m.end():m.end() + 60]))

    phones = list(RE_PHONE.finditer(text))
    phone_glued = sum(1 for m in phones if "\t" in m.group(0))

    print("РЕАЛЬНЫЙ ДОГОВОР — ТОЛЬКО КОЛИЧЕСТВА (содержимое не печатается)")
    print("  сегментов прочитано: %d, символов: %d\n" % (len(segments), len(text)))
    rows = [
        ("BIK", "9 цифр с «04», без слова «БИК» слева в 40 симв.", len(bik_bare),
         "всего таких прогонов %d" % len(bik_all)),
        ("KPP", "9 цифр, без слова «КПП» слева в 40 симв.", len(kpp_bare),
         "всего 9-значных прогонов %d" % len(kpp_all)),
        ("ACCOUNT", "«счёт-фактура» — якорь счёта в другом значении", len(invoices),
         "из них с 20-значным прогоном справа: %d" % inv_with_20),
        ("INN/INN_PER", "«ИНН … не присвоен / регистрационный номер»",
         len(RE_INN_ABSENT.findall(text)), ""),
        ("PHONE", "форма телефона С РАЗДЕЛИТЕЛЕМ ЯЧЕЙКИ внутри", phone_glued,
         "всего совпадений формы телефона %d" % len(phones)),
        ("PERCENT", "процент при слове технического параметра",
         len(RE_TECH_PCT.findall(text)), ""),
        ("TERM", "«в течение …» в предложении со ссылкой на норму",
         len(RE_TERM_NORM.findall(text)),
         "оборот «исковой давности»: %d" % len(RE_LIMITATION.findall(text))),
    ]
    print("  %-12s %-52s %6s   %s" % ("тип", "класс отрицательного примера", "штук", "рядом"))
    for t, what, n, extra in rows:
        print("  %-12s %-52s %6d   %s" % (t, what, n, extra))
    return 0


if __name__ == "__main__":
    sys.exit(main())
