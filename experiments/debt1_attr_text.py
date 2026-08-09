# -*- coding: utf-8 -*-
"""DEBT-1, часть 4 (NODES-ATTR-TEXT): ЗАМЕР — сколько текста живёт в АТРИБУТАХ.

Перепись NODES закрыла текст в ЭЛЕМЕНТАХ (w:t и т.п.), но не в атрибутах:
описания/названия рисунков (wp:docPr@descr/@name/@title, pic:cNvPr@descr),
свойства пакета (docProps/core.xml, app.xml), закладки, гиперссылки.

Считаем ЧАСТОТУ, а не чиним: ноль или единицы — доложить и не трогать.

    venv/Scripts/python.exe experiments/debt1_attr_text.py <каталог> [<каталог> …]
"""
import collections
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

CYR = re.compile(r"[А-Яа-яЁё]")
#: атрибуты, куда Word/Excel/PowerPoint кладут ЧЕЛОВЕЧЕСКИЙ текст
TEXT_ATTRS = {"descr", "title", "name", "alt", "tooltip", "val"}
#: части пакета со свойствами документа (там текст в ЭЛЕМЕНТАХ, но зона тоже
#: не читается извлечением — считаем отдельной строкой)
PROP_PARTS = ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml")

EXT = (".docx", ".xlsx", ".pptx")


def local(tag):
    return tag.rsplit("}", 1)[-1]


def scan_file(path, hits, prop_hits):
    try:
        z = zipfile.ZipFile(path)
    except Exception:
        return
    with z:
        for name in z.namelist():
            if not name.endswith(".xml"):
                continue
            try:
                root = ET.fromstring(z.read(name))
            except Exception:
                continue
            if name in PROP_PARTS:
                for el in root.iter():
                    txt = (el.text or "").strip()
                    if txt and CYR.search(txt):
                        prop_hits[(name, local(el.tag))].append((path, txt))
                continue
            for el in root.iter():
                for a, v in el.attrib.items():
                    an = local(a)
                    if an not in TEXT_ATTRS:
                        continue
                    v = (v or "").strip()
                    # только человеческий текст: кириллица + не служебное имя
                    if len(v) < 3 or not CYR.search(v):
                        continue
                    hits[(local(el.tag), an)].append((path, v))


def main():
    roots = sys.argv[1:]
    if not roots:
        print(__doc__)
        return 1
    for root_dir in roots:
        hits = collections.defaultdict(list)
        prop_hits = collections.defaultdict(list)
        n_files = 0
        for dirpath, _dirs, files in os.walk(root_dir):
            for f in files:
                if f.lower().endswith(EXT):
                    n_files += 1
                    scan_file(os.path.join(dirpath, f), hits, prop_hits)
        print("\n=== %s : %d файлов ===" % (root_dir, n_files))
        total = sum(len(v) for v in hits.values())
        print("  текст в АТРИБУТАХ: %d вхождений, %d видов (элемент@атрибут)"
              % (total, len(hits)))
        for (tag, attr), vals in sorted(hits.items(), key=lambda kv: -len(kv[1])):
            uniq = sorted({v for _p, v in vals})
            print("    %-18s @%-8s %5d вхожд., %d уникальных; напр.: %r"
                  % (tag, attr, len(vals), len(uniq), uniq[0][:60]))
        ptotal = sum(len(v) for v in prop_hits.values())
        print("  текст в СВОЙСТВАХ ПАКЕТА (docProps/*): %d вхождений, %d видов"
              % (ptotal, len(prop_hits)))
        for (part, tag), vals in sorted(prop_hits.items(), key=lambda kv: -len(kv[1])):
            uniq = sorted({v for _p, v in vals})
            print("    %-22s %-16s %5d вхожд., %d уникальных; напр.: %r"
                  % (part, tag, len(vals), len(uniq), uniq[0][:60]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
