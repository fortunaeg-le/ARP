"""Поиск документа корпуса, где движок ОСТАВИЛ ФАМИЛИЮ ОТКРЫТОЙ в нескольких
падежах — материал для пробы этапа MARKUP-SPREAD на настоящем документе."""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app"))
_HOME = tempfile.mkdtemp(prefix="spread-scan-")
os.environ["USERPROFILE"] = _HOME
os.environ["HOME"] = _HOME

import core                                       # noqa: E402
from anchor_registry import _lemma_first, _nameparts  # noqa: E402

docs = sorted((ROOT / "tests/corpus/docs").glob("*.docx"))
docs = [d for d in docs if "__m" not in d.name][:40]

for path in docs:
    try:
        res = core.run_encrypt(str(path))
    except Exception as exc:  # noqa: BLE001
        print("%-22s пропуск: %s" % (path.name, exc))
        continue
    anon = res["anon_text"]
    forms = {}
    for m in re.finditer(r"[А-ЯЁ][а-яё]{3,}", anon):
        w = m.group(0)
        if "surn" not in _nameparts(w):
            continue
        forms.setdefault(_lemma_first(w), set()).add(w)
    hits = {k: v for k, v in forms.items() if len(v) >= 2}
    if hits:
        print("%-22s ОТКРЫТЫЕ ФАМИЛИИ: %s" % (path.name, hits))
