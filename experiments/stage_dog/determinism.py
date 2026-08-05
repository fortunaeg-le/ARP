# -*- coding: utf-8 -*-
"""ЭТАП DOG Часть 3 — недетерминизм (EPRIME-A-CONFIRM), послойно.

Слои (отпечаток sha256, первые 16 hex):
  L1 текст сегментов            — что вообще извлечено из .docx
  L2 вход нейросети             — строки, поданные в Doc()/tag_ner (detection_view)
  L3 выход нейросети            — ВСЕ спаны NewsNERTagger: (тип, начало, конец)
  L4 выход адресного слоя       — сущности detect_ner
  L5 после арбитража            — весь run_detection
  L6 анонимный текст            — конечный результат (та самая «контрольная сумма»)

Наружу — только отпечатки, счётчики и координаты. Ни одного символа содержимого.

Запуск:
  determinism.py inproc N   — N прогонов в ОДНОМ процессе, печатает таблицу
  determinism.py one        — ОДИН прогон, печатает строку JSON (для мультипроцессного режима)
"""
import sys, io, json, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'C:\Jesus\ARP\src')

PATH = r'C:\shifrator_real\dog.docx'
CFG = r'C:\Jesus\ARP\entity_types.yaml'


def sha(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()[:16]


def _install_probe():
    """Оборачивает ner_detector.Doc, чтобы записать вход и выход нейросети."""
    import ner_detector
    real_doc = ner_detector.Doc
    log = {'inputs': [], 'spans': []}

    class ProbedDoc(real_doc):
        def tag_ner(self, tagger):
            log['inputs'].append(self.text)
            out = super().tag_ner(tagger)
            log['spans'].extend(
                (sp.type, sp.start, sp.stop) for sp in self.spans)
            return out

    ner_detector.Doc = ProbedDoc
    return log, (lambda: setattr(ner_detector, 'Doc', real_doc))


def one_run():
    from extractor import extract
    from pipeline import run_detection
    from tokenizer import tokenize
    import type_policy

    log, restore = _install_probe()
    try:
        doc = extract(PATH)
        L1 = sha('\u0000'.join(s.text for s in doc.segments))

        # прогон ЧЕРЕЗ detect_ner отдельно, чтобы снять L4 — но детекция
        # обязана быть той же самой, поэтому берём полный конвейер и
        # вытаскиваем адресный слой из общего результата по detector-метке.
        ents = run_detection(doc, CFG)

        L2 = sha('\u0000'.join(log['inputs']))
        L3 = sha(json.dumps(sorted(log['spans'])))
        ner_only = sorted(
            (e.entity_type, e.segment_id, e.start, e.end)
            for e in ents if getattr(e, 'detector', None) in ('ner', 'yargy'))
        L4 = sha(json.dumps(ner_only, ensure_ascii=False))
        allspans = sorted(
            (e.entity_type, s.segment_id, s.start, s.end)
            for e in ents for s in (e.spans if getattr(e, 'spans', None) else [e]))
        L5 = sha(json.dumps(allspans, ensure_ascii=False))

        enabled = type_policy.resolve('maximum', known=type_policy.known_types(CFG))
        anon, fin = tokenize(doc, ents, CFG, enabled_types=enabled)
        L6 = sha(anon)
        return {
            'L1_segments': L1, 'L2_nn_input': L2, 'L3_nn_output': L3,
            'L4_address': L4, 'L5_arbitrated': L5, 'L6_anon': L6,
            'n_nn_calls': len(log['inputs']), 'n_nn_spans': len(log['spans']),
            'n_entities': len(ents), 'n_masked': len(fin), 'anon_chars': len(anon),
        }
    finally:
        restore()


LAYERS = ['L1_segments', 'L2_nn_input', 'L3_nn_output',
          'L4_address', 'L5_arbitrated', 'L6_anon']


def report(rows, title):
    print('=== %s (%d прогонов) ===' % (title, len(rows)))
    hdr = '  %-14s' % 'слой' + ''.join('%-18s' % ('#%d' % (i + 1)) for i in range(len(rows)))
    print(hdr)
    first_diff = None
    for L in LAYERS:
        vals = [r[L] for r in rows]
        same = len(set(vals)) == 1
        if not same and first_diff is None:
            first_diff = L
        print('  %-14s' % L + ''.join('%-18s' % v for v in vals)
              + ('  ОДИНАКОВО' if same else '  <-- РАСХОЖДЕНИЕ (%d разных)' % len(set(vals))))
    for k in ('n_nn_calls', 'n_nn_spans', 'n_entities', 'n_masked', 'anon_chars'):
        vals = [r[k] for r in rows]
        print('  %-14s' % k + ''.join('%-18s' % v for v in vals)
              + ('  ОДИНАКОВО' if len(set(vals)) == 1 else '  <-- РАЗНЫЕ'))
    print('  ПЕРВОЕ РАСХОЖДЕНИЕ: %s' % (first_diff or 'нет — все слои совпали'))
    print()
    return first_diff


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'one':
        print(json.dumps(one_run()))
    elif mode == 'inproc':
        n = int(sys.argv[2])
        rows = []
        for i in range(n):
            rows.append(one_run())
            print('прогон %d готов' % (i + 1), file=sys.stderr)
        report(rows, 'ОДИН ПРОЦЕСС, %d прогонов подряд' % n)
    elif mode == 'collect':
        rows = [json.loads(l) for l in open(sys.argv[2], encoding='utf-8') if l.strip()]
        report(rows, 'РАЗНЫЕ ПРОЦЕССЫ')
