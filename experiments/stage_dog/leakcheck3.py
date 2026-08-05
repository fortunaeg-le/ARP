# -*- coding: utf-8 -*-
"""Приёмка (а), версия 2 — иглы, которые НЕ МОГУТ совпасть случайно.

Урок первой попытки: «редкое в документе длинное слово» — плохая игла.
«Ответственности», «обязательства» редки внутри договора, но обычны в любом
русском тексте, поэтому дали 926 ложных файлов.

Здесь иглы двух видов, оба практически не коллизионны:
  * СЛОВЕСНЫЕ 5-ГРАММЫ (пять слов подряд) — совпадение означает буквальный
    кусок текста документа, случайно так не сходится;
  * ЦИФРОВЫЕ ПРОГОНЫ >= 6 цифр — реквизиты, суммы, номера.

Печатаются только количества и, при совпадении, файл + вид иглы (слово/число)
и её длина. Сами иглы не печатаются никогда.
"""
import sys, io, re, os, subprocess, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'C:\Jesus\ARP\src')
from extractor import extract

REPO = r'C:\Jesus\ARP'
BASE = '0d8caf0'

text = '\n'.join(s.text for s in extract(r'C:\shifrator_real\dog.docx').segments)

# --- 5-граммы ---
toks = re.findall(r'[А-Яа-яЁёA-Za-z0-9]+', text)
grams = collections.Counter(
    ' '.join(toks[i:i + 5]) for i in range(0, len(toks) - 5, 7))
ngram_needles = [g for g, c in grams.items() if len(g) >= 30][:600]

# --- длинные числа ---
digit_needles = sorted({d for d in re.findall(r'(?<!\d)\d{6,}(?!\d)', text)})

needles = [('5-грамма', n) for n in ngram_needles] + \
          [('число', n) for n in digit_needles]
print('игл: %d (5-грамм %d, чисел %d)'
      % (len(needles), len(ngram_needles), len(digit_needles)))

tracked = [t for t in subprocess.run(
    ['git', '-C', REPO, 'ls-files'], capture_output=True, text=True,
    encoding='utf-8').stdout.split('\n') if t.strip()]
print('отслеживаемых файлов: %d' % len(tracked))

hits = []
for rel in tracked:
    path = os.path.join(REPO, rel.replace('/', os.sep))
    try:
        blob = open(path, 'rb').read()
    except OSError:
        continue
    s = None
    for enc in ('utf-8', 'cp1251'):
        try:
            s = blob.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    if s is None:
        continue
    for kind, n in needles:
        if n in s:
            hits.append((rel, kind, len(n)))

print()
print('=== 1. РАБОЧЕЕ ДЕРЕВО ===')
print('  совпадений: %d' % len(hits))
for rel, kind, ln in hits[:30]:
    print('    %s — игла вида «%s», длина %d' % (rel, kind, ln))

def scan_diff(args, label):
    d = subprocess.run(['git', '-C', REPO] + args, capture_output=True,
                       text=True, encoding='utf-8', errors='replace').stdout
    added = '\n'.join(l for l in d.split('\n') if l.startswith('+'))
    f = [(k, len(n)) for k, n in needles if n in added]
    print()
    print('=== %s ===' % label)
    print('  строк добавлено: %d' % len(added.split('\n')))
    print('  совпадений: %d' % len(f))
    for k, ln in f[:20]:
        print('    игла вида «%s», длина %d' % (k, ln))
    return f

a = scan_diff(['diff', BASE + '..HEAD'], '2. ДОБАВЛЕНО КОММИТАМИ СЕССИИ')
b = scan_diff(['diff', 'HEAD'], '3. НЕЗАКОММИЧЕННОЕ')

print()
print('ИТОГ: %s' % ('ЧИСТО' if not hits and not a and not b
                    else 'ЕСТЬ СОВПАДЕНИЯ — разбирать'))
