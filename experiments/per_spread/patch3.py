# -*- coding: utf-8 -*-
"""PER-SPREAD, уточнение F3.

Первая редакция обрывала прогон, если у него УЖЕ есть помета surn и помета
имени/отчества. Но токен-затравка сам может нести обе пометы сразу («Роман» —
и Name, и Surn), и тогда прогон, начатый с него, отказывался присоединить
НАСТОЯЩУЮ фамилию слева: «Макаров Роман Дмитриевич» распадался на
«Роман Дмитриевич» с ключом реестра ('роман',). Сравнение реестров до/после
поймало это шестью документами.

Признак «прогон уже содержит фамилию» усиливается до ЧИСТОЙ фамилии
(_pure_surname): двусмысленный токен фамилией прогона не считается.
"""
import io

P = "src/anchor_registry.py"
s = io.open(P, encoding="utf-8").read()


def sub(old, new):
    global s
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old, new, 1)


sub(
    '''    имя/отчество, на следующей ЧИСТОЙ фамилии останавливается. Проверка «чистая»
    (`_pure_surname`) обязательна: «Роман», «Павел» несут пометы и Name, и Surn —
    обрыв на них резал бы настоящие ФИО."""
    lo = hi = i
    real = 1 if not _is_initial(tokens[i].group(0)) else 0
    has_surn = "surn" in npar[i]
    has_given = bool(npar[i] & {"name", "patr"})

    def _next_person(k) -> bool:
        return has_surn and has_given and _pure_surname(npar[k])
''',
    '''    имя/отчество, на следующей ЧИСТОЙ фамилии останавливается.

    ЧИСТОТА (`_pure_surname`) проверяется С ОБЕИХ СТОРОН, и это не украшение.
    Справа: «Роман», «Павел» несут пометы и Name, и Surn — обрыв на них резал бы
    настоящие ФИО. Слева (в накопителе): токеном-затравкой прогона бывает тот же
    двусмысленный «Роман», и если считать его фамилией прогона, прогон откажется
    присоединить НАСТОЯЩУЮ фамилию слева — «Макаров Роман Дмитриевич» распадётся
    на «Роман Дмитриевич» с ключом реестра ('роман',). Поймано сравнением реестров
    до/после на шести документах корпуса, см. experiments/per_spread/keys_diff.py."""
    lo = hi = i
    real = 1 if not _is_initial(tokens[i].group(0)) else 0
    has_surn = _pure_surname(npar[i])
    has_given = bool(npar[i] & {"name", "patr"})

    def _next_person(k) -> bool:
        return has_surn and has_given and _pure_surname(npar[k])
''',
)

sub(
    '''        hi += 1
        if not is_init:
            real += 1
        has_surn = has_surn or "surn" in npar[hi]
        has_given = has_given or bool(npar[hi] & {"name", "patr"})''',
    '''        hi += 1
        if not is_init:
            real += 1
        has_surn = has_surn or _pure_surname(npar[hi])
        has_given = has_given or bool(npar[hi] & {"name", "patr"})''',
)

sub(
    '''        lo -= 1
        if not is_init:
            real += 1
        has_surn = has_surn or "surn" in npar[lo]
        has_given = has_given or bool(npar[lo] & {"name", "patr"})''',
    '''        lo -= 1
        if not is_init:
            real += 1
        has_surn = has_surn or _pure_surname(npar[lo])
        has_given = has_given or bool(npar[lo] & {"name", "patr"})''',
)

io.open(P, "w", encoding="utf-8", newline="\r\n").write(s)
print("уточнение F3 применено")
