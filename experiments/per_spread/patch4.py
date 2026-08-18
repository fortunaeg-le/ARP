# -*- coding: utf-8 -*-
"""PER-SPREAD, правка 4 — прогон ФИО не склеивает ДВЕ роли одного вида.

НАЙДЕНО ЗАМЕРОМ, а не рассуждением. После правки F3 пять документов корпуса
(lease_0006, works_0012 и три их мутации) стали утекать ХУЖЕ: утечка перешла
из «частичной» в «полную». Причина — проход 2, а не проход 1.

Ячейка: «Кузнецова Татьяна Сергеевна Кузнецова Татьяна Сергеевна».
Проход 1 кладёт спан на ПЕРВУЮ запись и останавливается перед второй фамилией
(это и есть F3). Проход 2 находит вторую «Кузнецову» по ключу реестра и зовёт
`expand_match` -> `_fio_run`, а тот расширяется НЕ ТОЛЬКО ВПРАВО (своё «Татьяна
Сергеевна»), но и ВЛЕВО — на «Сергеевну» ЧУЖОЙ, уже закрытой записи. Получившийся
спан пересекается с уже выставленной маской, движок его отбрасывает целиком
(`_covered`), и ВТОРАЯ запись остаётся открытой ПОЛНОСТЬЮ — вместо прежнего
«закрыта фамилия, открыты имя и отчество».

Лечится тем же структурным свойством ФИО, что и F3: в одном ФИО РОВНО ОДНО имя
и РОВНО ОДНО отчество. Прогон, у которого отчество уже есть, на втором отчестве
останавливается; то же для имени. Считаются только ОДНОЗНАЧНЫЕ токены (у которых
ровно одна помета): «Роман» несёт и Name, и Surn, и по нему рвать нельзя.
"""
import io

P = "src/anchor_registry.py"
s = io.open(P, encoding="utf-8").read()


def sub(old, new):
    global s
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old, new, 1)


sub(
    '''def _pure_surname(tags) -> bool:
    """Помета токена — ТОЛЬКО фамилия (не имя и не отчество). Такой токен не может
    быть продолжением уже собранного ФИО: он начинает СЛЕДУЮЩЕЕ."""
    return "surn" in tags and not (tags & {"name", "patr"})
''',
    '''def _pure_surname(tags) -> bool:
    """Помета токена — ТОЛЬКО фамилия (не имя и не отчество). Такой токен не может
    быть продолжением уже собранного ФИО: он начинает СЛЕДУЮЩЕЕ."""
    return "surn" in tags and not (tags & {"name", "patr"})


def _pure_role(tags):
    """ЕДИНСТВЕННАЯ помета токена ('surn' | 'name' | 'patr') или None, если помет
    нет либо их несколько. Двусмысленный токен («Роман» — и Name, и Surn) роли
    не имеет: рвать прогон по нему нельзя."""
    return next(iter(tags)) if len(tags) == 1 else None
''',
)

sub(
    '''    lo = hi = i
    real = 1 if not _is_initial(tokens[i].group(0)) else 0
    has_surn = _pure_surname(npar[i])
    has_given = bool(npar[i] & {"name", "patr"})

    def _next_person(k) -> bool:
        return has_surn and has_given and _pure_surname(npar[k])
''',
    '''    lo = hi = i
    real = 1 if not _is_initial(tokens[i].group(0)) else 0
    has_surn = _pure_surname(npar[i])
    has_given = bool(npar[i] & {"name", "patr"})
    # ЭТАП PER-SPREAD, правка 4: в одном ФИО РОВНО ОДНО имя и РОВНО ОДНО отчество.
    # Без этого счётчика проход 2, расширяя спан ВЛЕВО от найденной по реестру
    # фамилии, прихватывал отчество ЧУЖОЙ соседней записи, спан пересекался с уже
    # выставленной маской и отбрасывался целиком — вторая запись оставалась
    # открытой ПОЛНОСТЬЮ. Поймано замером: пять документов корпуса перешли из
    # частичной утечки в полную (см. experiments/per_spread/patch4.py).
    roles = collections.Counter()
    r0 = _pure_role(npar[i])
    if r0:
        roles[r0] += 1

    def _next_person(k) -> bool:
        if has_surn and has_given and _pure_surname(npar[k]):
            return True
        rk = _pure_role(npar[k])
        return rk in ("name", "patr") and roles[rk] >= 1
''',
)

sub(
    '''        hi += 1
        if not is_init:
            real += 1
        has_surn = has_surn or _pure_surname(npar[hi])
        has_given = has_given or bool(npar[hi] & {"name", "patr"})''',
    '''        hi += 1
        if not is_init:
            real += 1
        has_surn = has_surn or _pure_surname(npar[hi])
        has_given = has_given or bool(npar[hi] & {"name", "patr"})
        rk = _pure_role(npar[hi])
        if rk:
            roles[rk] += 1''',
)

sub(
    '''        lo -= 1
        if not is_init:
            real += 1
        has_surn = has_surn or _pure_surname(npar[lo])
        has_given = has_given or bool(npar[lo] & {"name", "patr"})''',
    '''        lo -= 1
        if not is_init:
            real += 1
        has_surn = has_surn or _pure_surname(npar[lo])
        has_given = has_given or bool(npar[lo] & {"name", "patr"})
        rk = _pure_role(npar[lo])
        if rk:
            roles[rk] += 1''',
)

sub("import re\nimport uuid\n", "import collections\nimport re\nimport uuid\n")

io.open(P, "w", encoding="utf-8", newline="\r\n").write(s)
print("правка 4 применена")
