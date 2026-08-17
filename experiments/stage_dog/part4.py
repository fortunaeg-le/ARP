# -*- coding: utf-8 -*-
"""ЭТАП DOG Часть 4 — что не нашлось, по классам. Наружу только числа и координаты.

Проверяются два названных подозрения:
  A. ИНН физлица, разорванный переносом, получает тип организации, а в наборе
     по умолчанию тип организации выключен -> значение уходит открытым.
  B. Адрес, разорванный переносом, не находится вовсе.

Плюс общий разбор: почему в договоре с ОГРН/КПП найдено 0 ИНН.
"""
import sys, io, os, re, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from collections import Counter

PATH = os.environ.get('ARP_REAL_DOC') or (sys.argv[1] if len(sys.argv) > 1 else None)
if not PATH:
    sys.exit('нужен путь к документу: задайте переменную окружения ARP_REAL_DOC=<путь> '
              'или передайте путь первым аргументом командной строки')
CFG = os.path.join(ROOT, 'entity_types.yaml')

from extractor import extract
from pipeline import run_detection
from tokenizer import tokenize
import type_policy

doc = extract(PATH)
ents = run_detection(doc, CFG)

segs = {s.id: s for s in doc.segments}

# ---------- покрытие: какие интервалы закрыты масками в каждом наборе ----------
cover = {}
for profile in ('personal', 'maximum'):
    enabled = type_policy.resolve(profile, known=type_policy.known_types(CFG))
    _anon, fin = tokenize(doc, ents, CFG, enabled_types=enabled)
    m = {}
    for e in fin:
        for sp in (e.spans if getattr(e, 'spans', None) else [e]):
            m.setdefault(sp.segment_id, []).append((sp.start, sp.end, e.entity_type))
    cover[profile] = m


def covered(profile, seg_id, s, e):
    for (a, b, t) in cover[profile].get(seg_id, []):
        if max(a, s) < min(b, e):
            return t
    return None


# ---------- 1. якорь «ИНН» и цифровые кандидаты ----------
ANCHOR = re.compile(r'(?i)\bинн\b')
# цифровой run с допустимыми разделителями паттерна (пробел / неразрывный пробел)
RUN = re.compile(r'\d(?:[ \u00a0]?\d)*')

anchor_hits = 0
cand = Counter()
anchor_cases = []          # якорь ИНН найден — что рядом
for sid, s in segs.items():
    txt = s.text
    if not txt:
        continue
    for m in ANCHOR.finditer(txt):
        anchor_hits += 1
        # окно справа от якоря, до конца строки
        tail = txt[m.end():m.end() + 60]
        nl = tail.find('\n')
        if nl >= 0:
            tail = tail[:nl]
        runs = [(mm.group(0), mm.start()) for mm in RUN.finditer(tail)]
        digits = [(len(re.sub(r'\D', '', r)), off) for r, off in runs]
        anchor_cases.append({
            'seg': sid, 'anchor_at': m.start(),
            'digit_runs_right': digits[:4],
            'masked_personal': covered('personal', sid, m.start(), m.end() + 40),
            'masked_maximum': covered('maximum', sid, m.start(), m.end() + 40),
            'seg_len': len(txt),
        })
    for mm in RUN.finditer(txt):
        n = len(re.sub(r'\D', '', mm.group(0)))
        if n in (10, 12):
            cand[n] += 1

print('=== 1. якорь «ИНН» в ПРОЧИТАННОМ тексте ===')
print('  вхождений слова ИНН        : %d' % anchor_hits)
print('  цифровых прогонов 10 цифр  : %d' % cand[10])
print('  цифровых прогонов 12 цифр  : %d' % cand[12])
print()
print('  разбор случаев с якорем (координаты и длины, без содержимого):')
for c in anchor_cases[:25]:
    print('    сегмент %-8s якорь@%-5d длины_цифр_справа=%s  personal=%s maximum=%s'
          % (c['seg'], c['anchor_at'], c['digit_runs_right'],
             c['masked_personal'], c['masked_maximum']))
if len(anchor_cases) > 25:
    print('    ... ещё %d' % (len(anchor_cases) - 25))

# ---------- 2. подозрение A: 12-значный ИНН, разорванный переносом ----------
print()
print('=== 2. подозрение A: ИНН физлица, разорванный переносом ===')
split_cases = []
for sid, s in segs.items():
    txt = s.text
    for m in re.finditer(r'(?i)\bинн\b', txt or ''):
        win = txt[m.end():m.end() + 60]
        if '\n' not in win:
            continue
        left, right = win.split('\n', 1)
        dl = len(re.sub(r'\D', '', left))
        dr = len(re.sub(r'\D', '', right[:20]))
        if dl and dr:
            split_cases.append((sid, m.start(), dl, dr))
print('  случаев «якорь ИНН, затем цифры, перенос, снова цифры»: %d' % len(split_cases))
for c in split_cases[:10]:
    print('    сегмент %-8s якорь@%-5d цифр_до_переноса=%d цифр_после=%d' % c)

# сколько сущностей типа INN/INN_PERSON нашла детекция вообще
print('  найдено детекцией INN=%d INN_PERSON=%d'
      % (sum(1 for e in ents if e.entity_type == 'INN'),
         sum(1 for e in ents if e.entity_type == 'INN_PERSON')))

# ---------- 3. подозрение B: адрес, разорванный переносом ----------
print()
print('=== 3. подозрение B: адрес, разорванный переносом ===')
ADDR_MARK = re.compile(
    r'(?i)(?<![а-яё])(ул\.|улица|г\.|город|д\.|дом|кв\.|пр-кт|проспект|пер\.|обл\.|область|'
    r'наб\.|стр\.|корп\.|шоссе|ш\.|индекс|респ\.|р-н)(?![а-яё])')
addr_ents = [e for e in ents if e.entity_type == 'ADDRESS']
addr_by_seg = {}
for e in addr_ents:
    for sp in (e.spans if getattr(e, 'spans', None) else [e]):
        addr_by_seg.setdefault(sp.segment_id, []).append((sp.start, sp.end))

mark_total = 0
mark_with_nl = 0
mark_with_nl_unfound = 0
unfound_rows = []
for sid, s in segs.items():
    txt = s.text
    if not txt:
        continue
    for m in ADDR_MARK.finditer(txt):
        mark_total += 1
        win_s, win_e = m.start(), min(len(txt), m.end() + 70)
        if '\n' not in txt[win_s:win_e]:
            continue
        mark_with_nl += 1
        hit = any(max(a, win_s) < min(b, win_e) for a, b in addr_by_seg.get(sid, []))
        if not hit:
            mark_with_nl_unfound += 1
            if len(unfound_rows) < 12:
                unfound_rows.append((sid, m.start(), len(txt)))
print('  адресных маркеров в прочитанном тексте        : %d' % mark_total)
print('  из них с переносом строки в окне 70 симв.     : %d' % mark_with_nl)
print('  из них НЕ покрыты ни одним ADDRESS-спаном     : %d' % mark_with_nl_unfound)
for r in unfound_rows:
    print('    сегмент %-8s маркер@%-5d длина_сегмента=%d' % r)
print('  всего ADDRESS найдено: %d' % len(addr_ents))

# ---------- 4. типы, не найденные вовсе ----------
print()
print('=== 4. типы без единой находки ===')
found = Counter(e.entity_type for e in ents)
known = type_policy.known_types(CFG)
print('  ', [t for t in known if not found.get(t)])
