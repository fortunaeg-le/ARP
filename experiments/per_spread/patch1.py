# -*- coding: utf-8 -*-
"""PER-SPREAD, правка 1 из 3 (F3): прогон ФИО не перебегает на СЛЕДУЮЩУЮ фамилию."""
import io

P = "src/anchor_registry.py"
s = io.open(P, encoding="utf-8").read()

OLD = '''def _fio_run(norm, tokens, npar, i):
    """Собирает СВЯЗНЫЙ спан ФИО вокруг токена i (у которого есть помета имени).
    Влево и вправо присоединяет соседние токены-части имени (surn/name/patr) и
    инициалы, если между ними только пробелы/точки. Возвращает (lo, hi) — диапазон
    ИНДЕКСОВ токенов [lo, hi], целиком составляющих ФИО."""
    lo = hi = i
    real = 1 if not _is_initial(tokens[i].group(0)) else 0
    # вправо
    while hi + 1 < len(tokens):
        gap = norm[tokens[hi].end():tokens[hi + 1].start()]
        if gap.strip(" \\t."):
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
        if gap.strip(" \\t."):
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
'''

NEW = '''def _pure_surname(tags) -> bool:
    """Помета токена — ТОЛЬКО фамилия (не имя и не отчество). Такой токен не может
    быть продолжением уже собранного ФИО: он начинает СЛЕДУЮЩЕЕ."""
    return "surn" in tags and not (tags & {"name", "patr"})


def _fio_run(norm, tokens, npar, i):
    """Собирает СВЯЗНЫЙ спан ФИО вокруг токена i (у которого есть помета имени).
    Влево и вправо присоединяет соседние токены-части имени (surn/name/patr) и
    инициалы, если между ними только пробелы/точки. Возвращает (lo, hi) — диапазон
    ИНДЕКСОВ токенов [lo, hi], целиком составляющих ФИО.

    ЭТАП PER-SPREAD — ГРАНИЦА ПРОГОНА. `per_search_view` заменяет перенос строки
    ПРОБЕЛОМ (иначе рвётся фамилия, перенесённая внутри слова), поэтому две строки
    ячейки «Кузнецова Татьяна Сергеевна\\nКузнецова Татьяна Сергеевна» для прогона
    неотличимы от одной фразы, а `_FIO_MAX_WORDS = 4` разрешает взять четвёртое
    слово. Спан ложился как «Кузнецова Татьяна Сергеевна Кузнецова», и ВТОРОЕ
    «Татьяна Сергеевна» оставалось открытым: маска короче значения, полнота при
    этом рапортует успех — ровно тот случай, ради которого утечка меряется отдельно
    от полноты. Лечится структурно, без опоры на перенос строки: ФИО не содержит
    ДВУХ ЧИСТЫХ фамилий подряд, поэтому прогон, у которого уже есть и фамилия, и
    имя/отчество, на следующей ЧИСТОЙ фамилии останавливается. Проверка «чистая»
    (`_pure_surname`) обязательна: «Роман», «Павел» несут пометы и Name, и Surn —
    обрыв на них резал бы настоящие ФИО."""
    lo = hi = i
    real = 1 if not _is_initial(tokens[i].group(0)) else 0
    has_surn = "surn" in npar[i]
    has_given = bool(npar[i] & {"name", "patr"})

    def _next_person(k) -> bool:
        return has_surn and has_given and _pure_surname(npar[k])

    # вправо
    while hi + 1 < len(tokens):
        gap = norm[tokens[hi].end():tokens[hi + 1].start()]
        if gap.strip(" \\t."):
            break
        w = tokens[hi + 1].group(0)
        is_init = _is_initial(w)
        if not (is_init or npar[hi + 1]):
            break
        if not is_init and real >= _FIO_MAX_WORDS:
            break
        if _next_person(hi + 1):
            break
        hi += 1
        if not is_init:
            real += 1
        has_surn = has_surn or "surn" in npar[hi]
        has_given = has_given or bool(npar[hi] & {"name", "patr"})
    # влево
    while lo - 1 >= 0:
        gap = norm[tokens[lo - 1].end():tokens[lo].start()]
        if gap.strip(" \\t."):
            break
        w = tokens[lo - 1].group(0)
        is_init = _is_initial(w)
        if not (is_init or npar[lo - 1]):
            break
        if not is_init and real >= _FIO_MAX_WORDS:
            break
        if _next_person(lo - 1):
            break
        lo -= 1
        if not is_init:
            real += 1
        has_surn = has_surn or "surn" in npar[lo]
        has_given = has_given or bool(npar[lo] & {"name", "patr"})
    return lo, hi
'''

assert s.count(OLD) == 1, s.count(OLD)
s = s.replace(OLD, NEW, 1)
io.open(P, "w", encoding="utf-8", newline="\r\n").write(s)
print("F3 применена")
