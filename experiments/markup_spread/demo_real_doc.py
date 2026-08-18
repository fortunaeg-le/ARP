"""Проба этапа MARKUP-SPREAD на НАСТОЯЩЕМ документе корпуса, дословный вывод.

Сценарий ровно пользовательский: зашифровать документ, найти значение, которое
движок ОСТАВИЛ ОТКРЫТЫМ и которое встречается несколько раз в разных падежах,
пометить ОДНО вхождение (как мышкой в интерфейсе), применить — и показать, что
закрылись все, а текст собирается обратно символ в символ.

Запуск: venv/Scripts/python.exe experiments/markup_spread/demo_real_doc.py <файл>
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app"))

# Изолированный профиль: реальный ~/.shifrator не трогаем.
_HOME = tempfile.mkdtemp(prefix="spread-demo-")
os.environ["USERPROFILE"] = _HOME
os.environ["HOME"] = _HOME

import core                                    # noqa: E402
from detokenizer import detokenize             # noqa: E402
from session_store import default_storage_dir  # noqa: E402
from storage import load_doc_segments          # noqa: E402

path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "tests/corpus/docs/labor_0004.docx")
WORD = sys.argv[2] if len(sys.argv) > 2 else None

res = core.run_encrypt(path)
sid = res["session_id"]
doc = load_doc_segments(sid)
anon = res["anon_text"]

print("ДОКУМЕНТ: %s" % path)
print("сегментов: %d, масок поставил движок: %d" % (len(doc.segments), res["entities_total"]))


def open_candidates():
    """Слова с заглавной, оставшиеся в обезличенном тексте больше одного раза в
    разных формах — кандидаты «движок пропустил»."""
    from anchor_registry import _lemma_first
    counts = {}
    for m in re.finditer(r"[А-ЯЁ][а-яё]{3,}", anon):
        counts.setdefault(_lemma_first(m.group(0)), []).append(m.group(0))
    return counts


if WORD is None:
    cands = [(k, v) for k, v in open_candidates().items() if len(set(v)) >= 2]
    cands.sort(key=lambda kv: -len(kv[1]))
    print("\nоткрытые повторяющиеся слова (лемма -> формы):")
    for k, v in cands[:10]:
        print("   %-16s %s" % (k, sorted(set(v))))
    if not cands:
        sys.exit("нечего помечать — открытых повторов нет")
    WORD = sorted(set(cands[0][1]))[0]

print("\nПОЛЬЗОВАТЕЛЬ ПОМЕЧАЕТ: %r (тип ФИО)" % WORD)
from storage import load_session               # noqa: E402
occupied = core._occupied_spans(load_session(sid))
loc = None
for seg in doc.segments:
    i = seg.text.find(WORD)
    while i != -1:
        s, e = i, i + len(WORD)
        if not any(max(a, s) < min(b, e) for (a, b) in occupied.get(seg.id, ())):
            loc = (seg.id, s, e)
            break
        i = seg.text.find(WORD, i + 1)
    if loc:
        break
if loc is None:
    sys.exit("значение %r не найдено в сегментах" % WORD)
print("выделено в сегменте %s [%d:%d]" % loc)

core.mark_missed(sid, loc[0], loc[1], loc[2], "PERSON")
applied = core.apply_pending_markup(sid)

print("\n--- ОТВЕТ apply_pending_markup (то, что получит интерфейс) ---")
print("spread_total = %d" % applied["spread_total"])
for r in applied["results"]:
    print("  правка %s: applied=%s spread_count=%d" % (r["kind"], r["applied"], r["spread_count"]))
    for sp in r["spread"]:
        seg = next(s for s in doc.segments if s.id == sp["segment_id"])
        print("     сегмент %-12s [%4d:%4d] %-18r  контекст: %r"
              % (sp["segment_id"], sp["start"], sp["end"], sp["surface"],
                 seg.text[max(0, sp["start"] - 25):sp["end"] + 15]))

new_anon = applied["anon_text"]
from anchor_registry import _lemma_first        # noqa: E402
key = _lemma_first(WORD)
left = sorted({m.group(0) for m in re.finditer(r"[А-Яа-яЁё]{3,}", new_anon)
               if _lemma_first(m.group(0)) == key})
print("\nосталось открытым в тексте после применения: %s" % (left or "ничего"))

from tokenizer import _assemble                # noqa: E402
plain = _assemble(doc, lambda s: s.text)       # исходный текст без единой маски
restored, unresolved = detokenize(new_anon, sid,
                                  storage_dir=str(default_storage_dir()), mode="exact")
same = restored == plain
print("\nВОССТАНОВЛЕНИЕ (режим exact): собралось СИМВОЛ В СИМВОЛ: %s; "
      "неразрешённых токенов: %s" % (same, unresolved or "нет"))
if not same:
    for i in range(min(len(plain), len(restored))):
        if plain[i] != restored[i]:
            print("  первое расхождение на позиции %d:\n    было: %r\n    стало: %r"
                  % (i, plain[i:i + 60], restored[i:i + 60]))
            break
    else:
        print("  длины разошлись: %d vs %d" % (len(plain), len(restored)))
