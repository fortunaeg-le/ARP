import glob, os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from extractor import extract
doc_id, needle = sys.argv[1], sys.argv[2]
path = glob.glob(os.path.join(ROOT, "tests/corpus/docs", doc_id + ".*"))[0]
doc = extract(path)
for s in doc.segments:
    t = s.text or ""
    if needle in t:
        i = t.index(needle)
        print("seg=%s  [%d]  ...%r..." % (s.id, i, t[max(0,i-90):i+90]))
