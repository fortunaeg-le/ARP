# -*- coding: utf-8 -*-
"""ЭТАП DOG Часть 1 — какую долю документа программа не читает.

Метод. Единица учёта — текстовый узел w:t (видимый текст). Для каждого
определяем, дойдёт ли он до extractor, ВОСПРОИЗВОДЯ правило чтения, а не
доверяя ему:

  extractor читает body.iterchildren() -> только w:p и w:tbl (src/extractor.py:667);
  текст абзаца = CT_P.text = xpath("w:r | w:hyperlink") -> ТОЛЬКО прямые дети w:p;
  ячейки таблиц верхнего уровня читаются, ВЛОЖЕННЫЕ таблицы — нет;
  части header*/footer*/footnotes/endnotes не открываются вовсе.

Наружу печатаются только числа, координаты и имена тегов. Ни одного символа
содержимого.
"""
import sys, io, os, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from collections import Counter
from ooxml_core import parse_xml, read_zip_parts

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
def w(t): return W + t

PATH = os.environ.get('ARP_REAL_DOC') or (sys.argv[1] if len(sys.argv) > 1 else None)
if not PATH:
    sys.exit('нужен путь к документу: задайте переменную окружения ARP_REAL_DOC=<путь> '
              'или передайте путь первым аргументом командной строки')
parts = read_zip_parts(PATH)


def ancestors(el):
    out = []
    p = el.getparent()
    while p is not None:
        out.append(p)
        p = p.getparent()
    return out


def classify(t_el):
    """Почему w:t не будет прочитан (или 'читается')."""
    anc = ancestors(t_el)
    tags = [a.tag for a in anc]

    # 1. в каком абзаце лежит
    try:
        p_idx = tags.index(w('p'))
    except ValueError:
        return 'вне w:p'                      # напр. w:t в неожиданном месте
    para = anc[p_idx]

    # 2. между w:t и его w:p не должно быть ничего, кроме w:r и (опц.) w:hyperlink
    between = anc[:p_idx]
    btags = [a.tag for a in between]
    allowed = {w('r'), w('hyperlink')}
    extra = [b.replace(W, 'w:') for b in btags if b not in allowed]
    if extra:
        return 'обёртка ' + '/'.join(dict.fromkeys(extra))

    # 3. сам абзац должен быть достижим: верхний уровень тела,
    #    либо ячейка НЕвложенной таблицы
    rest = anc[p_idx + 1:]
    rtags = [a.tag for a in rest]
    if rtags and rtags[0] == w('body'):
        return 'читается'
    n_tbl = rtags.count(w('tbl'))
    if w('tc') in rtags:
        if n_tbl > 1:
            return 'вложенная таблица'
        # абзац ячейки таблицы верхнего уровня
        after_tbl = rtags[rtags.index(w('tbl')) + 1:]
        if after_tbl and after_tbl[0] == w('body'):
            return 'читается'
        return 'таблица не на верхнем уровне: ' + '/'.join(
            x.replace(W, 'w:') for x in after_tbl[:2])
    return 'абзац не на верхнем уровне: ' + '/'.join(
        x.replace(W, 'w:') for x in rtags[:2])


root = parse_xml(parts['word/document.xml']).getroot()

# --- посимвольный учёт document.xml ---
by_reason = Counter()
chars = Counter()
for t in root.iter(w('t')):
    r = classify(t)
    by_reason[r] += 1
    chars[r] += len(t.text or '')

doc_total = sum(chars.values())
doc_read = chars['читается']

print('=== A. word/document.xml: видимый текст (узлы w:t) ===')
for r, c in chars.most_common():
    print('  %-34s узлов=%-6d символов=%-8d  %5.2f%%' % (r, by_reason[r], c, 100*c/doc_total))
print('  %-34s %s символов=%d' % ('ИТОГО', ' ' * 12, doc_total))

# --- прочие части ---
print()
print('=== B. части пакета, которые extractor не открывает вовсе ===')
other_total = 0
other_rows = []
for name in sorted(parts):
    if not (name.startswith('word/') and name.endswith('.xml')):
        continue
    stem = name[len('word/'):-4]
    if not (stem.startswith('header') or stem.startswith('footer')
            or stem in ('footnotes', 'endnotes', 'comments')):
        continue
    try:
        r2 = parse_xml(parts[name]).getroot()
    except Exception:
        other_rows.append((name, -1, -1)); continue
    txt = ''.join(x.text or '' for x in r2.iter(w('t')))
    n = sum(1 for _ in r2.iter(w('t')))
    other_rows.append((name, n, len(txt)))
    other_total += len(txt)
for name, n, c in other_rows:
    sig = 'значимая' if c and c > 0 and len(name) else ''
    print('  %-24s узлов=%-5d символов=%-6d' % (name, n, c))
print('  ИТОГО вне document.xml: %d символов' % other_total)

grand = doc_total + other_total
print()
print('=== C. ГЛАВНОЕ ЧИСЛО ===')
print('  весь видимый текст документа : %d символов' % grand)
print('  программа читает             : %d (%.2f%%)' % (doc_read, 100*doc_read/grand))
print('  программа НЕ читает          : %d (%.2f%%)' % (grand-doc_read, 100*(grand-doc_read)/grand))

# --- структурные счётчики ---
print()
print('=== D. структура ===')
all_p = list(root.iter(w('p')))
top_p = [c for c in root.find(w('body')).iterchildren() if c.tag == w('p')]
cell_p = [p for p in all_p if any(a.tag == w('tc') for a in ancestors(p))]
nested_p = [p for p in all_p
            if sum(1 for a in ancestors(p) if a.tag == w('tbl')) > 1]
print('  абзацев w:p всего в document.xml : %d' % len(all_p))
print('    из них верхний уровень тела    : %d  (читаются)' % len(top_p))
print('    из них в ячейках таблиц        : %d  (вложенных: %d)' % (len(cell_p), len(nested_p)))
print('    прочие                         : %d' % (len(all_p)-len(top_p)-len(cell_p)))
instr = list(root.iter(w('instrText')))
print('  полей Word (w:instrText)         : %d' % len(instr))
codes = Counter((x.text or '').strip().split(' ')[0].upper() for x in instr)
print('    коды инструкций (только имена)  : %s' % dict(codes.most_common(8)))
fld = list(root.iter(w('fldChar')))
print('  w:fldChar                        : %d' % len(fld))
hdr = [n for n in parts if n.startswith('word/header') or n.startswith('word/footer')]
print('  колонтитулов (частей)            : %d' % len(hdr))
