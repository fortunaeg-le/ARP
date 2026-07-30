# -*- coding: utf-8 -*-
"""
update_manifest.py — пересборка `tests/corpus/MANIFEST.sha256`.

ПОЧЕМУ НЕ ОБХОД КАТАЛОГА. Манифест корпуса — это ЯВНЫЙ, замороженный список
файлов (документы, модели, генераторы, эталон), а не «всё, что лежит в папке».
Каталог `tests/corpus/` вдобавок к охраняемому содержит расходное: дампы
прогонов `results_*.json` прошлых этапов, логи гейта, `results_gate_current.json`,
кеш питона. Обход `os.walk` затянул бы их внутрь и превратил бы контрольные
суммы в шум, который «расходится» после каждого прогона.

Поэтому состав берётся ИЗ САМОГО МАНИФЕСТА (список путей), плюс явный список
`GUARDED_EXTRA` — то, что этап решил взять под охрану дополнительно. Хеши
пересчитываются, состав не «угадывается».

ЧТО ДОБАВЛЕНО ЭТАПОМ T-GOLD-A И ЗАЧЕМ. До этого этапа в манифесте лежал эталон
(`gold.json`), но НЕ лежал ни один файл с сохранёнными результатами прогона.
Между тем именно `results_baseline.json` и `results_iter_baseline.json` — те
числа, с которыми гейт сравнивает текущий прогон. Не охраняемые ничем, они
позволяли сделать красный прогон зелёным правкой одной строки в JSON, не
оставив следа. Теперь оба под контрольной суммой: законная пересборка baseline
меняет и манифест, то есть видна в диффе как отдельное действие.

Запуск:
    venv/Scripts/python.exe tests/corpus/update_manifest.py            # записать
    venv/Scripts/python.exe tests/corpus/update_manifest.py --check    # сверить
"""
import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "MANIFEST.sha256")

# Файлы, взятые под охрану СВЕРХ исторического состава манифеста (этап T-GOLD-A).
# Оба — точки отсчёта регресс-гейта: полный прогон и прогон итерационного среза.
GUARDED_EXTRA = [
    "results_baseline.json",
    "results_iter_baseline.json",
    # Догонка этапа GATE-2: журнал обоснований линии «д» (порча документа).
    # Гейт сверяет уровень в точке отсчёта с последней записью журнала, поэтому
    # «поднять планку молча» = подправить ОБА файла. Под контрольной суммой это
    # перестаёт быть тихим действием: манифест меняется и виден в диффе.
    "overmask_ledger.json",
]


def read_manifest():
    """[(hash, rel)] в порядке файла."""
    rows = []
    with open(OUT, encoding="utf-8", newline="") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            h, rel = line.split("  ", 1)
            rows.append((h, rel))
    return rows


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build():
    """(текст нового манифеста, список путей, список добавленных)."""
    paths = [rel for _, rel in read_manifest()]
    added = [rel for rel in GUARDED_EXTRA if rel not in paths]
    paths = sorted(set(paths) | set(GUARDED_EXTRA))
    missing = [rel for rel in paths if not os.path.isfile(os.path.join(HERE, rel))]
    if missing:
        raise SystemExit(
            "нет файлов, которые манифест обязан охранять (%d): %s"
            % (len(missing), ", ".join(missing[:10]))
        )
    text = "".join("%s  %s\n" % (digest(os.path.join(HERE, rel)), rel)
                   for rel in paths)
    return text, paths, added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="сверить, ничего не записывая")
    args = ap.parse_args()

    text, paths, added = build()
    with open(OUT, encoding="utf-8", newline="") as f:
        old = f.read()

    if args.check:
        if text == old:
            print("MANIFEST.sha256: совпадает (%d файлов)" % len(paths))
            return 0
        old_map = dict((r[1], r[0]) for r in read_manifest())
        new_map = dict(line.split("  ", 1)[::-1] for line in text.splitlines())
        bad = sorted(r for r in set(old_map) | set(new_map)
                     if old_map.get(r) != new_map.get(r))
        print("MANIFEST.sha256: РАСХОЖДЕНИЙ %d из %d файлов" % (len(bad), len(paths)))
        for r in bad[:20]:
            print("   " + r)
        return 1

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("записано: %s" % OUT)
    print("  файлов в манифесте: %d" % len(paths))
    if added:
        print("  добавлено под охрану: %s" % ", ".join(added))
    return 0


if __name__ == "__main__":
    sys.exit(main())
