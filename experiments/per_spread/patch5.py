# -*- coding: utf-8 -*-
"""PER-SPREAD, правка 4' — вхождение не выбрасывается из-за левого крыла.

ЗАМЕНЯЕТ отменённую правку 4 (счётчик ролей внутри `_fio_run`). Та лечила ту же
болезнь, но в ПРОХОДЕ 1, где болезни нет: на замере она подняла недобор границ
PER на 12 сущностей / 142 символа и сняла одиночную защиту с одного значения.
Лечим ровно там, где ломается, — в ПРОХОДЕ 2.

БОЛЕЗНЬ. Ячейка «Кузнецова Татьяна Сергеевна Кузнецова Татьяна Сергеевна».
Проход 1 закрывает ПЕРВУЮ запись. Проход 2 находит вторую «Кузнецову» по ключу
реестра и зовёт `expand_match`, а тот собирает СВЯЗНЫЙ прогон ФИО в обе стороны —
вправо своё «Татьяна Сергеевна», влево ЧУЖУЮ «Сергеевну» уже закрытой записи.
Спан пересекается с выставленной маской, движок выбрасывает вхождение ЦЕЛИКОМ —
и вторая запись остаётся открытой ПОЛНОСТЬЮ (было: закрыта фамилия, открыты имя
и отчество; стало после F3: открыто всё).

ЛЕЧЕНИЕ. Пересечение — не приговор вхождению, а сигнал, что расширение уехало на
чужую территорию. Повторяем расширение БЕЗ ЛЕВОГО КРЫЛА: своё «Имя Отчество»
справа закрывается, чужая запись не трогается. Проход 1 не меняется вовсе.
"""
import io

P = "src/anchor_registry.py"
s = io.open(P, encoding="utf-8").read()


def sub(old, new):
    global s
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old, new, 1)


# 1. _fio_run учится не расширяться влево.
sub(
    '''def _fio_run(norm, tokens, npar, i):''',
    '''def _fio_run(norm, tokens, npar, i, no_left=False):''',
)
sub(
    '''    до/после на шести документах корпуса, см. experiments/per_spread/keys_diff.py."""''',
    '''    до/после на шести документах корпуса, см. experiments/per_spread/keys_diff.py.

    no_left=True — расширять ТОЛЬКО ВПРАВО. Нужно проходу 2, когда обычное
    расширение уехало влево на соседнюю УЖЕ ЗАКРЫТУЮ запись того же человека
    (две подряд идущие карточки в одной ячейке) и спан пересёкся с её маской."""''',
)
sub(
    '''    # влево
    while lo - 1 >= 0:
        gap = norm[tokens[lo - 1].end():tokens[lo].start()]''',
    '''    # влево
    while not no_left and lo - 1 >= 0:
        gap = norm[tokens[lo - 1].end():tokens[lo].start()]''',
)

# 2. Интерфейс детектора: expand_match принимает и передаёт no_left.
sub(
    '''    def expand_match(self, norm, low, tokens, npar, i, klen):
        """Проход 2: вокруг совпавшей фамилии собрать СВЯЗНЫЙ спан ФИО целиком —
        косвенный падеж («Морозовой Анне Николаевне») одним спаном (закрывает PER-B)."""
        lo, hi = _fio_run(norm, tokens, npar, i)
        return _fio_span(norm, tokens, lo, hi)''',
    '''    def expand_match(self, norm, low, tokens, npar, i, klen, no_left=False):
        """Проход 2: вокруг совпавшей фамилии собрать СВЯЗНЫЙ спан ФИО целиком —
        косвенный падеж («Морозовой Анне Николаевне») одним спаном (закрывает PER-B).
        no_left — повторная попытка движка, см. AnchorEngine._match_key."""
        lo, hi = _fio_run(norm, tokens, npar, i, no_left=no_left)
        return _fio_span(norm, tokens, lo, hi)''',
)

sub(
    '''    def expand_match(self, norm, low, tokens, npar, i, klen):''',
    '''    def expand_match(self, norm, low, tokens, npar, i, klen, no_left=False):''',
)

# 3. Движок: пересечение — повод сузить расширение, а не выбросить вхождение.
sub(
    '''            s, e = det.expand_match(norm, low, tokens, npar, i, klen)
            src_s, src_e = norm_to_src(omap, s, e)
            if self._covered(emitted_src, seg.id, src_s, src_e):
                i += klen
                continue''',
    '''            s, e = det.expand_match(norm, low, tokens, npar, i, klen)
            src_s, src_e = norm_to_src(omap, s, e)
            if self._covered(emitted_src, seg.id, src_s, src_e):
                # ЭТАП PER-SPREAD. Пересечение бывает ДВУХ РАЗНЫХ ПРИРОД, и
                # выбрасывать вхождение годится только для первой:
                #   1. это то же самое место, уже закрытое проходом 1 — выбросить;
                #   2. расширение уехало ВЛЕВО на соседнюю УЖЕ ЗАКРЫТУЮ запись того
                #      же человека («…Сергеевна | Кузнецова Татьяна Сергеевна» в
                #      одной ячейке) — тогда выброс оставляет ВТОРУЮ запись
                #      открытой ЦЕЛИКОМ, хотя своё «Имя Отчество» справа свободно.
                # Различаем пробой: повторяем расширение БЕЗ левого крыла. Если
                # суженный спан ни с чем не пересекается — это случай 2.
                s2, e2 = det.expand_match(norm, low, tokens, npar, i, klen,
                                          no_left=True)
                src_s2, src_e2 = norm_to_src(omap, s2, e2)
                if (s2, e2) == (s, e) or \\
                        self._covered(emitted_src, seg.id, src_s2, src_e2):
                    i += klen
                    continue
                s, e, src_s, src_e = s2, e2, src_s2, src_e2''',
)

io.open(P, "w", encoding="utf-8", newline="\r\n").write(s)
print("правка 4' применена")
