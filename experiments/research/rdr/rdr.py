# -*- coding: utf-8 -*-
"""R-RDR часть 1: дерево правил-исключений поверх текущего конвейера.

Метод (Compton & Jansen, ripple-down rules): правило добавляется ТОЛЬКО как
исключение под тем правилом, которое ошиблось, и вместе с правилом хранится
опорный случай (cornerstone). Новое правило обязано сохранить вердикт всех
опорных случаев ветки — тогда регрессия невозможна по построению. Здесь это
свойство не постулируется, а ПРОВЕРЯЕТСЯ прогоном по всему срезу.

Слой работает ПОВЕРХ конвейера и ничего в нём не меняет: он смотрит только на
куски текста, которые конвейер оставил без маски.
"""
import json, re, pathlib, sys
from collections import Counter, defaultdict

HERE = pathlib.Path(__file__).parent

# ── невидимые/омоглифы: минимальная нормализация значения ───────────────────
INVIS = "­​‌‍⁠  "
HOMO = {"О": "0", "о": "0", "З": "3", "з": "3", "б": "6", "І": "1", "і": "1",
        "Ч": "4", "ч": "4", "l": "1", "O": "0", "o": "0"}


def digits(s):
    """цифровое поле значения: снять переносы, невидимые и цифровые омоглифы"""
    out = []
    for ch in s:
        if ch in INVIS or ch in "\n\r\t ":
            continue
        out.append(HOMO.get(ch, ch))
    return "".join(out)


def clean(s):
    return "".join(ch for ch in s if ch not in INVIS)


# ── генератор кандидатов: значение после метки-якоря ────────────────────────
# Метка — то, что в договоре печатают перед реквизитом. Кандидат — окно ПОСЛЕ
# метки; перевод строки внутри окна не разрывает кандидата (в этом весь смысл).
ANCHORS = [
    ("подразделение", r"код\s+подразделе\s*\n?\s*ния"),
    ("подразделение", r"код\s+подразделения"),
    ("телефон",       r"(?:по\s+телефону|тел\s*\.?\s*склада\s*:|тел\s*\.?\s*:|тел\s*\.?|телефон\s*:?)"),
    ("почта",         r"e\s*-?\s*mail\s*:?"),
    ("бик",           r"бик(?:\s+банка)?\s*:?"),
    ("инн",           r"инн\s*:?"),
    ("кпп",           r"кпп\s*:?"),
    ("огрн",          r"огрнип\s*:?|огрн\s*:?"),
    ("счёт",          r"р\s*/\s*\n?\s*с|расч[её]тный\s+сч[её]т\s*:?"),
    ("корсчёт",       r"к\s*/\s*\n?\s*с"),
    ("снилс",         r"снилс\s*:?"),
    ("рождение",      r"дата\s+рождения\s*:?"),
    ("контакт",       r"\bконтакт\b\s*:?"),
    ("ответственный", r"ответственн(?:ый|ое)(?:\s+лицо)?\s*:?"),
]
ANCHOR_RE = [(n, re.compile(p, re.I)) for n, p in ANCHORS]
WIN = 46


def candidates(text):
    """(имя якоря, начало значения, окно) для каждой метки в тексте"""
    out = []
    for name, rx in ANCHOR_RE:
        for m in rx.finditer(text):
            s = m.end()
            while s < len(text) and text[s] in " \t:.,;":
                s += 1
            out.append({"anchor": name, "s": s, "win": text[s:s + WIN],
                        "before": text[max(0, m.start() - 40):m.start()]})
    return out


# ── правила ────────────────────────────────────────────────────────────────
# Каждое правило: условие(cand) -> (тип, длина значения в ИСХОДНОМ тексте).
# Длина считается «прочитать окно, пока не набрано нужное число цифр/символов».

def _homo_at(win, i):
    """ИСКЛЮЧЕНИЕ E1 (под числовыми правилами): буква считается омоглифом цифры
    ТОЛЬКО рядом с цифрой. Без этой оговорки «О» из «ОГРН» читалось как «0» и
    числовое значение не кончалось никогда (кпп срабатывал 2 раза из 19)."""
    ch = win[i]
    if ch not in HOMO:
        return ch
    for j in (i - 1, i + 1):
        if 0 <= j < len(win) and win[j].isdigit():
            return HOMO[ch]
    return ch


def take_digits(win, n):
    """сколько символов окна надо съесть, чтобы набрать n цифр (переносы не в счёт)"""
    got = 0
    for i, ch in enumerate(win):
        c = _homo_at(win, i)
        if c.isdigit():
            got += 1
            if got == n:
                return i + 1
        elif ch in INVIS or ch in "\n\r\t -/()":
            continue
        else:
            return None
    return None


def num_rule(n_digits, gtype, extra=None):
    def f(c):
        if extra and not extra(c):
            return None
        ln = take_digits(c["win"], n_digits)
        if ln is None:
            return None
        # значение не должно продолжаться ещё одной цифрой (иначе это не тот реквизит)
        tail = c["win"][ln:]
        j = 0
        while j < len(tail) and (tail[j] in INVIS or tail[j] in " \n\r\t"):
            j += 1
        if j < len(tail) and _homo_at(tail, j).isdigit():
            return None
        return (gtype, ln)
    return f


def r_ogrn(c):
    """ОГРН 13 цифр; ИСКЛЮЧЕНИЕ E2: если сразу за 13-й идёт ещё цифра — это
    ОГРНИП, 15 цифр (иначе правило молчало на 23 окнах из 38)."""
    v = num_rule(13, "OGRN")(c)
    if v is not None:
        return v
    ln = take_digits(c["win"], 15)
    return ("OGRN", ln) if ln is not None else None


def r_birth_date(c):
    """ИСКЛЮЧЕНИЕ E3 под «дата рождения»: точка между цифрами — часть значения,
    а не его конец (общий числовой сканер обрывался на первой точке)."""
    win = c["win"]
    got = 0
    for i, ch in enumerate(win):
        ch2 = _homo_at(win, i)
        if ch2.isdigit():
            got += 1
            if got == 8:
                return ("BIRTHDATE", i + 1)
        elif ch in INVIS or ch in " \n\r\t.-/":
            continue
        else:
            return None
    return None


CYR_MAIL = re.compile(r"^[A-Za-zА-Яа-яЁё0-9._%\-]+@[\sA-Za-zА-Яа-яЁё0-9.\-\n" + INVIS +
                      r"]{3,30}?\.[A-Za-zА-Яа-яЁё]{2,4}")


def r_mail_cyr(c):
    """ИСКЛЮЧЕНИЕ E4 под «e-mail»: домен с кириллическими омоглифами
    (volkov@yаndex.ru — «а» кириллическая) — тот же адрес, а не проза."""
    m = CYR_MAIL.match(clean(c["win"]))
    if not m:
        return None
    need = len(m.group(0)); got = 0
    for i, ch in enumerate(c["win"]):
        if ch not in INVIS:
            got += 1
        if got == need:
            return ("EMAIL", i + 1)
    return None


PHONE_RE = re.compile(r"^[\s\n+()0-9\-" + INVIS + r"]{9,22}")
MAIL_RE = re.compile(r"^[A-Za-z0-9._%\-]+@[\sA-Za-z0-9.\-\n" + INVIS + r"]{4,30}?\.[a-z]{2,4}")
FIO_RE = re.compile(r"^[А-ЯЁ][а-яё]+(?:\s*\n?\s*[А-ЯЁ]\.\s*[А-ЯЁ]\.|"
                    r"\s*\n?\s*[А-ЯЁ][а-яё]+\s*\n?\s*[А-ЯЁ][а-яё]+)")
FIO_CAPS_RE = re.compile(r"^[А-ЯЁ]{2,}\s*\n?\s*[А-ЯЁ]{2,}\s*\n?\s*[А-ЯЁ]{2,}")
DATE_RE = re.compile(r"^[\d\s\n.]{8,14}")


def r_phone(c):
    m = PHONE_RE.match(c["win"])
    if not m:
        return None
    v = digits(m.group(0))
    if not (10 <= len(re.sub(r"\D", "", v)) <= 11):
        # укоротить до 11 цифр
        ln = take_digits(c["win"], 11) or take_digits(c["win"], 10)
        return ("PHONE", ln) if ln else None
    return ("PHONE", len(m.group(0)))


def r_mail(c):
    m = MAIL_RE.match(clean(c["win"]))
    if not m:
        return None
    # длину считаем по исходному окну: невидимые уже сняты, поэтому идём заново
    need = len(m.group(0))
    got = 0
    for i, ch in enumerate(c["win"]):
        if ch not in INVIS:
            got += 1
        if got == need:
            return ("EMAIL", i + 1)
    return None


def r_person(c):
    for rx, t in ((FIO_RE, "PER"), (FIO_CAPS_RE, "PER")):
        m = rx.match(c["win"].lstrip())
        if m:
            pad = len(c["win"]) - len(c["win"].lstrip())
            return (t, pad + len(m.group(0)))
    return None


def r_birth(c):
    m = DATE_RE.match(c["win"])
    if not m:
        return None
    if len(re.sub(r"\D", "", digits(m.group(0)))) < 8:
        return None
    ln = take_digits(c["win"], 8)
    return ("BIRTHDATE", ln) if ln else None


def r_div(c):
    ln = take_digits(c["win"], 6)
    return ("PASSPORT", ln) if ln else None


def r_inn(c):
    """ИНН: 10 цифр — организация, 12 — человек (T2-INN)."""
    ln12 = take_digits(c["win"], 12)
    if ln12 is not None:
        return ("INN_PER", ln12)
    ln10 = take_digits(c["win"], 10)
    return ("INN", ln10) if ln10 is not None else None


def r_snils(c):
    ln = take_digits(c["win"], 11)
    return ("SNILS", ln) if ln else None


# дерево: якорь -> корневое правило, у правила — список исключений
TREE = {
    "подразделение": {"rule": r_div, "except": []},
    "телефон":       {"rule": r_phone, "except": []},
    "почта":         {"rule": r_mail, "except": [r_mail_cyr]},
    "бик":           {"rule": num_rule(9, "BIK"), "except": []},
    "инн":           {"rule": r_inn, "except": []},
    "кпп":           {"rule": num_rule(9, "KPP"), "except": []},
    "огрн":          {"rule": num_rule(13, "OGRN"), "except": [r_ogrn]},
    "счёт":          {"rule": num_rule(20, "ACCOUNT"), "except": []},
    "корсчёт":       {"rule": num_rule(20, "ACCOUNT"), "except": []},
    "снилс":         {"rule": r_snils, "except": []},
    "рождение":      {"rule": r_birth, "except": [r_birth_date]},
    "контакт":       {"rule": r_person, "except": []},
    "ответственный": {"rule": r_person, "except": []},
}


def apply_tree(c):
    """Корневое правило якоря; если его вердикт оказался неверен на опорном
    случае — спрашиваем исключения, пристёгнутые ИМЕННО к нему (E1 сидит внутри
    числового сканера и поэтому в списке не значится)."""
    node = TREE.get(c["anchor"])
    if not node:
        return None
    v = node["rule"](c)
    if v is not None:
        return v
    for ex in node["except"]:
        v = ex(c)
        if v is not None:
            return v
    return None


# E1 (омоглиф только рядом с цифрой) — исключение под всеми числовыми правилами,
# живёт в _homo_at; считаем его отдельным правилом.
GLOBAL_EXCEPTIONS = 1


def n_rules():
    return len(TREE) + sum(len(n["except"]) for n in TREE.values()) + GLOBAL_EXCEPTIONS
