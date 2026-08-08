# -*- coding: utf-8 -*-
"""ЭТАП STORE, часть 4 — структурная запись ручной разметки вместо сырого текста.

Зачем. Разметка (U3) — долгоживущий актив: она копится неделями и нужна как
эталонный материал для будущего анализатора. Но до этого этапа каждая запись
несла поле `value` — ФРАГМЕНТ РЕАЛЬНОГО ТЕКСТА открытым видом, то есть ПДн,
пережившие сессию, из-за которой они появились. Получалось, что инструмент,
созданный убирать ПДн из текста, сам накапливал их дольше всех.

Что вместо. Запись хранит признаки фрагмента, а не фрагмент: тип, границы,
форму токенов, морфологические пометы, контекст через БЕЛЫЙ СПИСОК служебных
слов и обе стороны решения — что предложила система и что решил человек.
Проверка сделанности простая: по записи нельзя восстановить исходное значение,
но можно ответить на вопросы, ради которых разметка и копится, — «на каких
формах ошибается детектор», «в каком месте документа», «человек сузил границу
или расширил».

Про белый список отдельно. Контекст берётся НЕ вычитанием запрещённого, а
сложением разрешённого: в записи оседают только слова из списка служебных
(`_FUNCTION_WORDS`) — предлоги, союзы, частицы и роль-слова бланка («инн»,
«адрес», «тел»). Всё остальное заменяется на «·». Чёрный список тут не годится
принципиально: он пропускает то, чего в нём не предусмотрели, а ПДн — ровно
такой класс, где непредусмотренное и опасно.

Морфология не обязательна: без natasha пометы будут None, и это честно
записано в поле, а не подменено выдумкой.
"""

import re

# --------------------------------------------------------------------------- #
# Белый список: только эти слова могут попасть в контекст записи как есть.
# --------------------------------------------------------------------------- #
_FUNCTION_WORDS = frozenset("""
в во на за по из от до у к с со о об обо при над под перед про через для без
между около возле вдоль мимо после кроме сверх ввиду вследствие
и а но да или либо ни то же ли бы не нет что чтобы как когда если хотя так тоже
также затем потом далее выше ниже именно только лишь даже уже ещё был была было
были быть есть является являются
г гор с д дом кв корп стр ул пер пр просп пл наб ш обл р-н респ край окр
инн кпп огрн огрнип бик кс рс окпо окато оквэд снилс
паспорт серия номер выдан выдано код подразделения
тел телефон факс почта email e-mail сайт
договор приложение пункт п пп статья ст лицо лицом стороны сторона
адрес зарегистрирован проживает место рождения дата рожд рождения
руб рублей коп копеек ндс годовых дней месяцев лет процентов
ооо оао зао ао пао ип нко ано фгуп мбу гбу
""".split())

_WORD_RE = re.compile(r"[\w-]+", re.UNICODE)
_DIGIT_RE = re.compile(r"\d")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

#: Сколько слов контекста брать с каждой стороны. Больше не нужно: контекст
#: здесь отвечает на «в каком обороте стоял фрагмент», а не пересказывает абзац.
_CONTEXT_WORDS = 4

#: Заглушка на месте слова, не прошедшего белый список.
_REDACTED = "·"


def _case_shape(word: str) -> str:
    """Облик слова одним кодом, без самого слова."""
    if not word:
        return "empty"
    if word.isdigit():
        return "digits"
    letters = [c for c in word if c.isalpha()]
    if not letters:
        return "other"
    if all(c.isupper() for c in letters):
        return "upper"
    if letters[0].isupper() and all(c.islower() for c in letters[1:]):
        return "title"
    if all(c.islower() for c in letters):
        return "lower"
    return "mixed"


def value_case_shape(value: str) -> str:
    """Облик ФРАГМЕНТА ЦЕЛИКОМ одним кодом.

    Считается по словам, а не по объединению обликов токенов: «Иванов И.И.» —
    это `title` (все слова с заглавной), хотя токен «И.И.» сам по себе `upper`.
    Единственная реализация на продукт: `app/report.py` берёт её отсюда, чтобы
    записи разных поколений не получали разный код за одну и ту же строку.
    """
    if not isinstance(value, str):
        value = ""
    letters = [c for c in value if c.isalpha()]
    if not letters:
        return "no_letters"
    if all(c.isupper() for c in letters):
        return "upper"
    if all(c.islower() for c in letters):
        return "lower"
    words = [w for w in value.split() if any(c.isalpha() for c in w)]
    if words and all(w[0].isupper() for w in words if w[0].isalpha()):
        return "title"
    return "mixed"


def token_shapes(value: str) -> list[dict]:
    """Форма КАЖДОГО токена значения: длина, облик регистра, классы символов.

    Именно потокенно, а не одной сводкой на весь фрагмент: «Иванов И.И.» и
    «ИВАНОВ ИВАН» отличаются формой токенов, и по агрегату этой разницы не
    видно — а разбираться будут как раз в ней.
    """
    out = []
    for tok in value.split():
        out.append({
            "length_chars": len(tok),
            "case_shape": _case_shape(tok),
            "has_digits": bool(_DIGIT_RE.search(tok)),
            "has_latin": bool(_LATIN_RE.search(tok)),
            "has_cyrillic": bool(_CYRILLIC_RE.search(tok)),
            "has_punct": bool(_PUNCT_RE.search(tok)),
            # Инициалы: заглавная с точкой, возможно цепочкой («И.», «И.И.»).
            # Точка обязательна — иначе под признак попало бы любое «ООО».
            "is_initial": bool(re.fullmatch(r"[А-ЯЁA-Z]\.(?:\s?[А-ЯЁA-Z]\.?)*", tok)),
        })
    return out


_morph_vocab = None
_morph_failed = False


def _get_morph():
    """MorphVocab natasha лениво и один раз. Недоступна — работаем без неё."""
    global _morph_vocab, _morph_failed
    if _morph_vocab is not None or _morph_failed:
        return _morph_vocab
    try:
        from natasha import MorphVocab
        _morph_vocab = MorphVocab()
    except Exception:  # noqa: BLE001
        _morph_failed = True
    return _morph_vocab


def morph_marks(value: str) -> list[dict] | None:
    """Морфологические пометы по токенам: часть речи, падеж, число, род.

    Возвращает None, если морфология недоступна, — «не смогли» и «нет помет»
    обязаны различаться, иначе позже это прочтут как «слово без падежа».
    Сама лемма НЕ сохраняется: она и есть исходное слово.
    """
    vocab = _get_morph()
    if vocab is None:
        return None
    out = []
    for tok in value.split():
        word = tok.strip(".,;:()«»\"'")
        if not word:
            out.append({"pos": None, "case": None, "number": None, "gender": None})
            continue
        try:
            forms = vocab(word)
        except Exception:  # noqa: BLE001
            forms = []
        feats = forms[0].feats if forms else {}
        out.append({
            "pos": forms[0].pos if forms else None,
            "case": feats.get("Case"),
            "number": feats.get("Number"),
            "gender": feats.get("Gender"),
        })
    return out


def whitelisted_context(segment_text: str, start: int, end: int) -> dict:
    """Соседние слова через белый список: служебное — как есть, прочее — «·».

    Возвращает {"before": [...], "after": [...]}. Регистр слова не сохраняется
    (список сравнивается по нижнему), чтобы через облик служебного слова не
    просачивалась информация о соседях.
    """
    if not isinstance(segment_text, str):
        return {"before": [], "after": []}

    def _filtered(chunk: str, take_last: bool) -> list[str]:
        words = _WORD_RE.findall(chunk)
        words = words[-_CONTEXT_WORDS:] if take_last else words[:_CONTEXT_WORDS]
        return [w.lower() if w.lower() in _FUNCTION_WORDS else _REDACTED for w in words]

    return {
        "before": _filtered(segment_text[:max(start, 0)], take_last=True),
        "after": _filtered(segment_text[max(end, 0):], take_last=False),
    }


def build_record(
    kind: str,
    value: str | None,
    segment_text: str | None,
    start: int | None,
    end: int | None,
    proposed: dict,
    decided: dict,
) -> dict:
    """Структурная запись разметки. Исходное значение НЕ возвращается ни в одном поле.

    `proposed` — что предложила система (тип, границы, каким механизмом),
    `decided` — что решил человек. Обе стороны хранятся отдельно: без них
    запись отвечает «что в документе», но не «в чём система ошиблась», а
    разметка копится именно ради второго.
    """
    value = value if isinstance(value, str) else ""
    return {
        "format": "structural-1",
        "kind": kind,
        "span": {
            "start": start,
            "end": end,
            # Длина САМОГО фрагмента, а не разности границ: границы приходят из
            # разных систем координат (сегмент документа против панели), и
            # выводить длину вычитанием значило бы иногда получать чужое число.
            "length_chars": len(value),
            "tokens": len(value.split()),
        },
        "case_shape": value_case_shape(value),
        "token_shapes": token_shapes(value),
        "morph": morph_marks(value),
        "context": whitelisted_context(segment_text or "", start or 0, end or 0),
        "proposed": proposed,
        "decided": decided,
    }
