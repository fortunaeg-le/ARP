"""Разбор конкретных отказов guard-ов (диагностика этапа MARKUP-SPREAD, не замер)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import markup_spread as MS                                   # noqa: E402
from models import SourceDocument, TextSegment               # noqa: E402

gold = {e["doc_id"]: e for e in
        json.load(open(ROOT / "tests/corpus/gold.json", encoding="utf-8"))}

CASES = [("agency_0003", "ООО Ромашка-Плюс", "ORG"),
         ("agency_0001", "ПАО «ПромСнаб»", "ORG"),
         ("labor_0005", "ПАО Альтаир", "ORG")]

for doc_id, want, ui_type in CASES:
    entry = gold[doc_id]
    text = (ROOT / "tests/corpus/docs" / (doc_id + ".txt")).read_text(encoding="utf-8")
    seg = TextSegment(id="s0", text=text, source_type="txt_line", metadata={})
    doc = SourceDocument(segments=[seg], source_format="txt", source_path=doc_id)
    anchor = next(x for x in entry["entities"] if x["text"] == want)
    norm, low, omap = MS._view_for(ui_type, seg)
    from normalizer import src_to_norm
    a_ns, a_ne = src_to_norm(omap, anchor["start"], anchor["end"])
    a_text = norm[a_ns:a_ne].strip()
    a_flags = MS._context_flags(norm, low, a_ns, a_ne)
    print("=" * 70)
    print("%s | якорь %r ns=%d flags=%s" % (doc_id, a_text, a_ns, sorted(a_flags)))
    key = MS._key_of(a_text)
    print("  ключ:", key)
    for (ns, ne) in MS._morph_spans(norm, key):
        f = MS._context_flags(norm, low, ns, ne)
        ok = MS._accept(norm, low, ns, ne, a_flags, a_text[:1].isupper(), len(key) == 1)
        print("  кандидат ns=%-6d %-40r flags=%-16s принят=%s  слева=%r"
              % (ns, norm[ns:ne], sorted(f), ok, norm[max(0, ns - 30):ns]))
