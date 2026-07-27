# -*- coding: utf-8 -*-
"""
build_structure.py — ОДНОРАЗОВЫЙ сканер структурных конфигураций `.docx` корпуса.

Зачем отдельным файлом, а не полем в gold.json
----------------------------------------------
`gold.json` входит в `MANIFEST.sha256` (656 файлов). Добавление в него поля
меняет байты, ломает манифест и обесценивает всю историю байтовых сверок —
docs/PERF_REPORT.md, раздел «Что при этом теряется», п.6 прямо предписывает
держать структуру в отдельном файле рядом. Результат пишется в
`tests/corpus/structure.json`; `gold.json` НЕ читается и НЕ трогается.

Метод — независимый XML-сканер (ZIP + regex по `word/document.xml` и списку
частей), БЕЗ импорта `src/extractor.py`: структура документа должна считаться
независимо от того, как её видит наш собственный парсер (иначе баг парсера
спрячет от нас же класс документов). Набор признаков — тот же, что в
docs/PERF_REPORT.md §3.3.

Запуск (пересчёт нужен только если в корпус добавили/заменили `.docx`):
    venv/Scripts/python.exe tests/corpus/build_structure.py
    venv/Scripts/python.exe tests/corpus/build_structure.py --check

`--check` ничего не пишет: пересчитывает и сравнивает с записанным
`structure.json`, код возврата 1 при расхождении.
"""
import argparse
import json
import os
import re
import sys
import zipfile

from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
OUT = os.path.join(HERE, "structure.json")

# Порядок признаков ФИКСИРОВАН: он входит в ключ конфигурации, который
# сохраняется в structure.json и сравнивается тестом покрытия.
#
# ДВА КЛЮЧА, и это не избыточность.
#   `config`     — ключ БЕЗ `intraword_run`. Даёт ровно 14 конфигураций с
#                  распределением 28/23/23/21/13/13/11/9/7/4/4/2/2/2 —
#                  побайтно то, что перечислено в docs/PERF_REPORT.md §3.3.
#                  Это контракт: «14 структурных конфигураций» во всех
#                  документах проекта означает именно его.
#   `config_ext` — тот же ключ ПЛЮС `intraword_run`: **25** конфигураций.
#
# ИСТОРИЯ ЧИСЛА (важно, потому что в старых документах проекта стоит другое).
#   * PERF_REPORT §3.3 сообщал «разрыв ранов внутри слова — 162 (100%)»: у той
#     сессии признак оказался константой и в разбиение не входил вовсе.
#   * Предыдущая сессия считала признак по соседним `w:t` и получила 74/162,
#     а значит 21 конфигурацию. Это определение НЕВЕРНО — см. подробный разбор
#     трёх дефектов в `_intraword_run_LOOSE` ниже.
#   * Строгое определение (разрыв между РАНАМИ, без w:br/w:tab между ними,
#     абзацы разбираются деревом) даёт **48/162** и **25** конфигураций.
# Число 25 пересчитано, а не унаследовано. Ни одно из трёх чисел не подгонялось
# под другое: 162 не воспроизводится, 21 признано ошибочным, 25 — текущий
# результат строгого определения. Срез обязан покрывать оба ключа (25 ⊃ 14).
FEATURE_ORDER = [
    "table",            # w:tbl
    "nested_table",     # w:tbl внутри w:tc
    "textbox",          # w:txbxContent (надпись)
    "footnote",         # w:footnoteReference
    "header",           # word/header*.xml
    "footer",           # word/footer*.xml
    "line_break",       # w:br
    "intraword_run",    # разрыв ранов внутри слова
    "ins",              # w:ins  (в корпусе — 0 документов)
    "del",              # w:del  (в корпусе — 0)
    "fld_simple",       # w:fldSimple / w:instrText (в корпусе — 0)
    "smart_tag",        # w:smartTag (в корпусе — 0)
    "sdt",              # w:sdt (в корпусе — 0)
]

_RE_TBL = re.compile(r"<w:tbl[ >]")
_RE_TC = re.compile(r"<w:tc[ >]")
_RE_TC_END = re.compile(r"</w:tc>")
_RE_TXBX = re.compile(r"<w:txbxContent[ >]")
_RE_FOOTREF = re.compile(r"<w:footnoteReference[ />]")
_RE_BR = re.compile(r"<w:br[ />]")
_RE_INS = re.compile(r"<w:ins[ >]")
_RE_DEL = re.compile(r"<w:del[ >]")
_RE_FLD = re.compile(r"<w:fldSimple[ >]|<w:instrText[ >]")
_RE_SMART = re.compile(r"<w:smartTag[ >]")
_RE_SDT = re.compile(r"<w:sdt[ >]")
_RE_P = re.compile(r"<w:p[ >]")
_RE_PARA = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_RE_T = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)

# Слово, разорванное между ранами: конец одного <w:t> и начало следующего в том
# же абзаце — оба непробельные словесные символы.
_WORDCH = re.compile(r"[0-9A-Za-zЀ-ӿ]")


def _nested_table(xml):
    """w:tbl, встреченный внутри открытой ячейки w:tc."""
    depth = 0
    for m in re.finditer(r"<w:tc[ >]|</w:tc>|<w:tbl[ >]", xml):
        tok = m.group(0)
        if tok.startswith("<w:tc"):
            depth += 1
        elif tok == "</w:tc>":
            depth = max(0, depth - 1)
        elif depth > 0:
            return True
    return False


def _intraword_run_LOOSE(xml):
    """ПРЕЖНЕЕ, НЕВЕРНОЕ определение. Оставлено только как документация ошибки;
    в scan() не вызывается. Три дефекта, каждый даёт ложное «слово разорвано»:

      1. `<w:t>` берутся подряд, БЕЗ УЧЁТА ГРАНИЦЫ РАНА. Но «слово разорвано
         ФОРМАТИРОВАНИЕМ» — это разрыв между РАНАМИ (`w:r`): у них разные
         свойства. Два `w:t` внутри ОДНОГО рана форматирование не меняют.
      2. Между двумя `<w:t>` может стоять `<w:br/>` или `<w:tab/>` — тогда
         это перевод строки или табуляция, а не разрыв слова. Регулярка их
         не видит вовсе: в корпусе `w:br` появляется внутри одного рана
         (чанк с `\\n`), и обе половины строки склеивались в «слово».
      3. `_RE_PARA` нежадная и на вложенных абзацах (надпись `w:txbxContent`
         внутри `w:p`) обрывает абзац на ВНУТРЕННЕМ `</w:p>`, поэтому границы
         абзацев считались неверно.

    Итог: 74 документа из 162 против 48 по строгому определению; 26 документов
    помечались разорванными без основания, и структурных конфигураций
    получалось 21 вместо 25.
    """
    for para in _RE_PARA.findall(xml):
        texts = _RE_T.findall(para)
        for a, b in zip(texts, texts[1:]):
            if not a or not b:
                continue
            if _WORDCH.match(a[-1]) and _WORDCH.match(b[0]):
                return True
    return False


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_BRK = "\x00"       # служебный маркер w:br / w:tab внутри рана


def _intraword_run(xml_bytes):
    """СТРОГОЕ определение «слово разорвано форматированием».

    Слово считается разорванным, если в ОДНОМ абзаце есть два соседних
    непустых РАНА (`w:r` — прямые дети `w:p`, поэтому вложенные абзацы
    считаются отдельно), между текстами которых нет `w:br`/`w:tab`, и при этом
    последний символ первого и первый символ второго — оба словесные.

    Разбор — деревом (lxml), а не регуляркой: вложенность `w:p` внутри
    `w:txbxContent` регуляркой корректно не режется. Независимость от
    src/extractor.py сохранена: здесь свой обход, наш собственный парсер не
    участвует.
    """
    root = etree.fromstring(xml_bytes)
    for p in root.iter(_W + "p"):
        prev = None
        for r in p:
            if r.tag != _W + "r":
                continue                      # прямые дети — только раны
            buf = []
            for el in r.iter():
                if el.tag == _W + "t":
                    buf.append(el.text or "")
                elif el.tag in (_W + "br", _W + "tab"):
                    buf.append(_BRK)
            s = "".join(buf)
            if not s:
                continue
            if prev is not None and prev[-1] != _BRK and s[0] != _BRK \
                    and _WORDCH.match(prev[-1]) and _WORDCH.match(s[0]):
                return True
            prev = s
    return False


def _para_bucket(n):
    """Корзина по числу абзацев (в корпусе все документы попадают в 51-100)."""
    if n <= 10:
        return "1-10"
    if n <= 50:
        return "11-50"
    if n <= 100:
        return "51-100"
    if n <= 500:
        return "101-500"
    return "500+"


def scan(path):
    with zipfile.ZipFile(path) as z:
        parts = z.namelist()
        raw = z.read("word/document.xml")
    xml = raw.decode("utf-8", "replace")
    f = {
        "table": bool(_RE_TBL.search(xml)),
        "nested_table": _nested_table(xml),
        "textbox": bool(_RE_TXBX.search(xml)),
        "footnote": bool(_RE_FOOTREF.search(xml)),
        "header": any(re.match(r"word/header\d*\.xml$", p) for p in parts),
        "footer": any(re.match(r"word/footer\d*\.xml$", p) for p in parts),
        "line_break": bool(_RE_BR.search(xml)),
        "intraword_run": _intraword_run(raw),
        "ins": bool(_RE_INS.search(xml)),
        "del": bool(_RE_DEL.search(xml)),
        "fld_simple": bool(_RE_FLD.search(xml)),
        "smart_tag": bool(_RE_SMART.search(xml)),
        "sdt": bool(_RE_SDT.search(xml)),
    }
    n_para = len(_RE_P.findall(xml))
    return f, n_para


def config_key(features, para_bucket, with_intraword=False):
    """Строковый ключ конфигурации — детерминированный и человекочитаемый."""
    on = [k for k in FEATURE_ORDER
          if features[k] and (with_intraword or k != "intraword_run")]
    return "|".join(on) + "#p:" + para_bucket


def build():
    names = sorted(f for f in os.listdir(DOCS) if f.lower().endswith(".docx"))
    docs = {}
    for fn in names:
        feats, n_para = scan(os.path.join(DOCS, fn))
        bucket = _para_bucket(n_para)
        docs[os.path.splitext(fn)[0]] = {
            "features": feats,
            "n_paragraphs": n_para,
            "para_bucket": bucket,
            "config": config_key(feats, bucket),
            "config_ext": config_key(feats, bucket, with_intraword=True),
        }
    configs = sorted({d["config"] for d in docs.values()})
    configs_ext = sorted({d["config_ext"] for d in docs.values()})
    return {
        "_comment": ("Структурные конфигурации .docx корпуса. Считается "
                     "build_structure.py, НЕ полем gold.json (gold в MANIFEST). "
                     "Пересчитывать только при изменении состава tests/corpus/docs. "
                     "config — 14 конфигураций PERF_REPORT §3.3 (без intraword_run); "
                     "config_ext — 25 (со СТРОГИМ intraword_run: разрыв между "
                     "ранами, а не между w:t; прежнее определение давало 21 и "
                     "было неверным). Срез покрывает оба."),
        "feature_order": FEATURE_ORDER,
        "n_docx": len(docs),
        "configs": configs,
        "configs_ext": configs_ext,
        "docs": docs,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="не писать, сверить с существующим structure.json")
    args = ap.parse_args()
    data = build()
    n_iw = sum(1 for d in data["docs"].values() if d["features"]["intraword_run"])
    print("docx: %d | config (PERF §3.3): %d | config_ext: %d | intraword_run: %d/%d"
          % (data["n_docx"], len(data["configs"]), len(data["configs_ext"]),
             n_iw, data["n_docx"]))
    for key in ("config", "config_ext"):
        counts = {}
        for d in data["docs"].values():
            counts[d[key]] = counts.get(d[key], 0) + 1
        print("--- %s (%d) ---" % (key, len(counts)))
        for cfg in sorted(counts, key=lambda c: (-counts[c], c)):
            print("  %3d  %s" % (counts[cfg], cfg))
    if args.check:
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)
        same = (json.dumps(old.get("docs"), sort_keys=True)
                == json.dumps(data["docs"], sort_keys=True))
        print("сверка с structure.json: %s" % ("OK" if same else "РАСХОЖДЕНИЕ"))
        sys.exit(0 if same else 1)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("записано: %s" % OUT)


if __name__ == "__main__":
    main()
