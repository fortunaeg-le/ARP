# -*- coding: utf-8 -*-
"""
gate.py — ЕДИНЫЙ регресс-гейт SHIFRATOR (этап 0d, расширен этапом S2).

Один прогон корпуса — семь охраняемых линий + креши + MANIFEST. До этапа S2
линий было две штуки в двух файлах: `gate.py` (leak/FP/masking, база
`results_baseline.json`) и `experiments/stage_d/gate_d.py` (precision/долг, база
`baseline_d.json`) — полная картина существовала только в голове человека,
запустившего оба, а `gate_d` можно было запустить по устаревшему дампу
(ARCHITECTURE_AUDIT §4.7, §5.8). Теперь база ОДНА (`results_baseline.json` —
полный дамп прогона, из него считаются ВСЕ метрики) и прогон ОДИН.

СЕМЬ ЛИНИЙ (любая красная -> exit 1); «д» и «е» добавлены этапом GATE-2,
«ж» — этапом T4:

  (а) PRECISION не упал ниже baseline − допуск — по КАЖДОМУ типу эталона.
      FP = kept-маска на ОБЪЯВЛЕННОМ негативе, TP = kept-маска на gold своего
      типа (единая дефиниция этапа D, measure_lib.precision_by_type). Ловит
      «стали маскировать лишнее» — то, к чему слепа masking C.
      ЭТАП GATE-2: линия держала только NER-хребет (ORG/PER/ADDRESS), а все
      реквизиты были вне охраны — их точность могла упасть до нуля незаметно.
  (б) LEAK_V2 не вырос — по каждому ИЗ 17 типов отдельно И по агрегату
      BIK-excl, оба порога (>=6 и >=8). Ловит «стали хуже прятать ПДн».
  (в) MASKING A (round-trip) не упала И равна 100%. Инвариант: замаскированное
      значение обязано восстанавливаться байт-в-байт.
  (г) MASKING B (границы: эталон закрыт масками целиком) не упала.
      C (тип маски) — МЯГКИЙ уровень по решению владельца: печатается,
      падение даёт ПРЕДУПРЕЖДЕНИЕ, гейт НЕ роняет (gate_config.MASKING_C_WARN_PP).
  (д) OVER-MASK ПРОЗЫ не вырос — по каждому типу и по агрегату. Маска, не
      легшая НИ на эталонную сущность, НИ на объявленный негатив, закрыла
      обычный текст (номер пункта договора, кусок прозы). Гейт такие маски
      печатал, но НЕ СРАВНИВАЛ: порча документа проходила зелёной в любом
      объёме. Набор неоднозначен (банки-контрагенты корпус не размечает) —
      см. обоснование в gate_config.OVERMASK_NOTHING_TOLERANCE.
  (е) ГРАНИЦЫ ПО НАПРАВЛЕНИЮ ОШИБКИ, два числа раздельно, по каждому типу:
      НЕДОБОР (маска короче эталона — часть значения открыта, УТЕЧКА) и
      ПЕРЕБОР (маска длиннее эталона — закрыт лишний текст, ПОРЧА). Линия «г»
      (masking B) видит ТОЛЬКО недобор: маска шире эталона по B не нарушение
      вообще, поэтому перебор мог расти неограниченно при зелёном гейте.
      Прибор этапа ADDR-B (experiments/stage_addr_b/addr_probe.py) считал это
      для ADDRESS; здесь та же логика (тот же код measure_lib) для всех типов.
  (ж) ЗЕРКАЛО ПОДАВЛЕНИЯ (этап T4) — число эталонных сущностей, погашенных
      отрицательным классом (CLAUSE_REF/ROLE_TERM/COLLECTIVE), обязано быть
      БЕЗУСЛОВНЫМ НУЛЁМ. Это единственная линия без допуска и без сравнения с
      baseline: барьер, съевший настоящую ПДн-сущность, — не регресс на дельту,
      а сразу дефект. Реализация — compare_suppression().

Линии «в»/«г» проверяются И ПО АГРЕГАТУ, И ПО КАЖДОМУ ТИПУ МАСКИ ОТДЕЛЬНО.
Причина — находка этапа S2: на дельте 2b -> HEAD агрегат B вырос 79.59% ->
86.79%, а ADDRESS-B при этом ОБВАЛИЛСЯ 75.33% -> 51.39% — агрегат вырос лишь
потому, что у ADDRESS упало число масок, то есть его вес в знаменателе.
Обвал целого типа спрятался за ростом среднего. Правило «по каждому типу
отдельно», с самого начала действовавшее для leak_v2, распространено на
masking.

ПЛЮС, независимыми условиями:
  1. КРЕШИ. outcome != "processed". Порог 0.
  3. FP ВСЕГО на размеченных негативах — не вырос сверх gate_config.FP_TOLERANCE.
  4. КОРПУС. sha256sum -c MANIFEST.sha256 — OK до И после прогона.
  5. ИЗВЕСТНЫЙ ДОЛГ (docs/known_leaks_stage_c.json): ADDRESS found=False & leak.
     Порог мягкий (решение владельца, этап D), компенсатор — ДИАГНОСТИКА
     СОСТАВА: счёт тот же, но множество сменилось -> ПРЕДУПРЕЖДЕНИЕ, чтобы
     подмена «N на другой N» была видна, а не растворялась в допуске.

Улучшения (leak/FP упали, masking/precision выросли) гейт НЕ роняют — печатаются.

Допуски — в gate_config.py, каждый с обоснованием. Все нулевые: разброс между
повторными прогонами на неизменном src/ ИЗМЕРЕН и равен нулю (этап E″ +
проверка этапа S2), значит допуск не компенсировал бы шум, а прощал бы регресс.

Запуск (минуты, полный корпус encrypt+decrypt по 324 документам — НЕ вешать
на pre-commit, место этого гейта — CI на PR, трогающем src/):
    venv/Scripts/python.exe tests/corpus/gate.py
    venv/Scripts/python.exe tests/corpus/gate.py --results tests/corpus/results_head_s2.json
      (второй вид — оценить УЖЕ СДЕЛАННЫЙ прогон, не гоняя корпус заново;
       файл прогона обязан быть свежим, гейт этого проверить не может)

Условие 1 (креши) — строгое НАДМНОЖЕСТВО того, что проверяет `pytest -m slow`
(tests/test_corpus_no_crash.py::test_full_corpus_encrypt_never_crashes). Если
gate.py уже запущен в этой сессии — отдельный `pytest -m slow` НЕ нужен, это
второй полный прогон Natasha ради уже покрытой проверки
(docs/archive/reports/EFFICIENCY_AUDIT.md §2.1).

Логика гейта (все четыре линии + креши + долг) не покрыта прогоном корпуса —
только собственными юнит-тестами, см. tests/corpus/test_gate_regression_detection.py
(быстрые, без Natasha): каждая линия краснеет от подложенного синтетического
регресса. Гейт, который никогда не краснел, непроверен.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import measure_lib as ML  # noqa: E402
import run_measurement as RM  # noqa: E402
import gate_config as GC  # noqa: E402
import overmask_ledger as OL  # noqa: E402

MANIFEST = os.path.join(HERE, "MANIFEST.sha256")
BASELINE = os.path.join(HERE, "results_baseline.json")
CURRENT_DUMP = os.path.join(HERE, "results_gate_current.json")
KNOWN_LEAKS = os.path.join(ROOT, "docs", "known_leaks_stage_c.json")

EMPTY_STATS = {"n": 0, "found": 0, "leak_v1": 0, "leak_v2_6": 0, "leak_v2_8": 0}


#: Каталоги, состав которых охраняется ЦЕЛИКОМ. Не список руками: берётся из
#: самого манифеста — верхний каталог каждого его пути с '/'. Сегодня это
#: `docs/` (замороженный корпус v1) и `_model/`; заведут третий — он попадёт
#: под охрану сам, без правки этого файла.
#: Не в счёт: служебное (`__pycache__`) и всё, что корпусом не является.
_IGNORED_DIRS = {"__pycache__", ".git", ".pytest_cache"}


def _manifest_rows():
    rows = []
    with open(MANIFEST, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            digest, rel = line.split(None, 1)
            rows.append((digest, rel.replace("\\", "/")))
    return rows


def _scan_guarded_dirs(rows):
    """Фактический состав охраняемых каталогов -> множество путей от HERE."""
    dirs = sorted({rel.split("/", 1)[0] for _, rel in rows if "/" in rel})
    found = set()
    for d in dirs:
        root = os.path.join(HERE, d)
        if not os.path.isdir(root):
            continue
        for cur, subdirs, files in os.walk(root):
            subdirs[:] = [s for s in subdirs if s not in _IGNORED_DIRS]
            for name in files:
                full = os.path.join(cur, name)
                found.add(os.path.relpath(full, HERE).replace("\\", "/"))
    return dirs, found


def check_manifest():
    """Независимая от shell coreutils реализация `sha256sum -c MANIFEST.sha256`
    (нужна на Windows, где sha256sum не всегда есть в PATH) ПЛЮС сверка состава
    каталога в обе стороны. Возвращает (ok, problems, n_checked).

    ЭТАП FIX-2 — ПОЧЕМУ ОДНОЙ КОНТРОЛЬНОЙ СУММЫ БЫЛО МАЛО. Манифест — БЕЛЫЙ
    СПИСОК: он читает имена файлов ИЗ САМОГО СЕБЯ, поэтому видел ровно два из
    трёх событий — «файл изменился» и «файл исчез». Третье, «файл ПОЯВИЛСЯ»,
    не видел вовсе: положи в `tests/corpus/docs/` лишний документ — и проверка
    осталась бы зелёной, а замороженный корпус тихо перестал бы быть тем, на
    чём снята точка отсчёта. Запрет 2 корневого `CLAUDE.md` говорит «не
    редактировать, НЕ ДОПОЛНЯТЬ, не удалять» — держались из трёх слов два.
    Теперь состав каталога сверяется в ОБЕ стороны, и лишний файл краснит гейт
    так же, как изменённый."""
    problems = []
    rows = _manifest_rows()
    n = 0
    for digest, rel in rows:
        n += 1
        path = os.path.join(HERE, rel)
        if not os.path.isfile(path):
            problems.append(f"{rel}: файл отсутствует")
            continue
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != digest:
            problems.append(f"{rel}: sha256 не совпадает с MANIFEST.sha256")

    # --- сверка состава в обе стороны (этап FIX-2) ---
    dirs, found = _scan_guarded_dirs(rows)
    listed = {rel for _, rel in rows if rel.split("/", 1)[0] in dirs}
    for rel in sorted(found - listed):
        problems.append(
            f"{rel}: ФАЙЛ ПОЯВИЛСЯ в охраняемом каталоге, манифест его не знает "
            "— замороженный корпус дополнен")
    return (not problems), problems, n


def _rate(hits, n):
    return (100.0 * hits / n) if n else 0.0


def _delta_mark(before, after):
    """'+' — стало лучше (утечка/FP упали), '-' — стало хуже (выросли), ' ' — не изменилось."""
    if after < before:
        return "+"
    if after > before:
        return "-"
    return " "


# --------------------------------------------------------------------------- #
#   Линия «б» + креши + FP + masking A/B/C — историческое ядро (этап 0d)       #
# --------------------------------------------------------------------------- #
def compare(baseline_agg, current_agg, fp_tolerance,
            leak_tolerance=0, masking_a_pp=0.0, masking_b_pp=0.0,
            masking_c_pp=0.0):
    """Возвращает (rows, regressions, improvements) — БЕЗ warnings, историческая
    сигнатура сохранена (её зовёт test_gate_regression_detection). Полную оценку
    четырёх линий даёт evaluate(); эта функция закрывает креши, линию «б»
    (leak_v2), FP и линии «в»/«г» (masking A/B; C — мягкий, в warnings evaluate).

    rows — построчные данные для печати диф-таблицы (все 14 типов).
    regressions/improvements — списки человекочитаемых строк."""
    regressions = []
    improvements = []

    # ЭТАП INSTR, ЧАСТЬ 2. Линия крешей смотрит СТРОГО на outcome == "crashed"
    # (aggregate_results отделяет это от "refused" — честного отказа политикой
    # непрочитанных зон, см. docstring aggregate_results). До этой правки любой
    # outcome != "processed" считался и печатался как один и тот же "КРЕШИ" —
    # рост отказов маскировался под рост крешей, а реальный креш мог затеряться
    # в ожидаемом счёте отказов. Отдельный, не блокирующий счётчик отказов —
    # в evaluate() (warnings), не здесь: у compare() историческая сигнатура
    # (rows, regressions, improvements), её зовёт test_gate_regression_detection.
    if len(current_agg["crashed"]) > len(baseline_agg["crashed"]):
        new_crashes = sorted(set(current_agg["crashed"]) - set(baseline_agg["crashed"]))
        regressions.append(
            "КРЕШИ: %d документ(ов) упали с необработанным исключением (было %d): %s"
            % (len(current_agg["crashed"]), len(baseline_agg["crashed"]),
               ", ".join(new_crashes[:20]) + (" …" if len(new_crashes) > 20 else ""))
        )

    rows = []
    for t in ML.ALL_ENTITY_TYPES:
        b = baseline_agg["per_type"].get(t, dict(EMPTY_STATS))
        c = current_agg["per_type"].get(t, dict(EMPTY_STATS))
        rows.append((t, b, c))
        for key, label in (("leak_v2_6", "leak_v2>=6"), ("leak_v2_8", "leak_v2>=8")):
            if c[key] > b[key] + leak_tolerance:
                regressions.append(
                    "(б) %s[%s]: %d -> %d (+%d сущностей) — рост частичной утечки"
                    % (t, label, b[key], c[key], c[key] - b[key])
                )
            elif c[key] < b[key]:
                improvements.append(
                    "%s[%s]: %d -> %d (-%d)" % (t, label, b[key], c[key], b[key] - c[key])
                )

    bt, ct = baseline_agg["total_bik_excl"], current_agg["total_bik_excl"]
    for key, label in (("leak_v2_6", "leak_v2>=6"), ("leak_v2_8", "leak_v2>=8")):
        if ct[key] > bt[key] + leak_tolerance:
            regressions.append(
                "(б) TOTAL(BIK-excl)[%s]: %d -> %d (+%d сущностей) — рост частичной утечки"
                % (label, bt[key], ct[key], ct[key] - bt[key])
            )
        elif ct[key] < bt[key]:
            improvements.append(
                "TOTAL(BIK-excl)[%s]: %d -> %d (-%d)" % (label, bt[key], ct[key], bt[key] - ct[key])
            )

    fp_b = baseline_agg["fp_on_neg_total"]
    fp_c = current_agg["fp_on_neg_total"]
    fp_delta = fp_c - fp_b
    if fp_delta > fp_tolerance:
        regressions.append(
            "(3) FP по негативам: %d -> %d (+%d), допуск gate_config.FP_TOLERANCE=+%d превышен"
            % (fp_b, fp_c, fp_delta, fp_tolerance)
        )
    elif fp_delta < 0:
        improvements.append("FP по негативам: %d -> %d (%d)" % (fp_b, fp_c, fp_delta))

    # --- линии «в»/«г»: masking_correctness (A жёстко, B жёстко, C мягко) ---
    mb = baseline_agg.get("masking_correctness", {}).get("total")
    mc = current_agg.get("masking_correctness", {}).get("total")
    if not mb or not mb["n_masks"]:
        # baseline снят до появления метрики (нет поля "masks") — сравнивать не с
        # чем. Молчать нельзя: иначе линии «в»/«г» «зелены» просто потому, что их
        # не с чем сопоставить.
        regressions.append(
            "masking_correctness: в baseline нет поля 'masks' — пересоберите "
            "results_baseline.json текущим run_measurement.py, иначе линии «в»/«г» слепы"
        )
    elif mc and mc["n_masks"]:
        (ab, bb, cb), (ac, bc_, cc) = ML.mc_rates(mb), ML.mc_rates(mc)
        for line, label, before, after, tol in (
            ("в", "A round-trip", ab, ac, masking_a_pp),
            ("г", "B границы", bb, bc_, masking_b_pp),
        ):
            if after < before - tol - 1e-9:
                regressions.append(
                    "(%s) masking_correctness[%s]: %.2f%% -> %.2f%% — корректность "
                    "маскировки УПАЛА (допуск %.2fпп)" % (line, label, before, after, tol)
                )
            elif after > before + 1e-9:
                improvements.append(
                    "masking_correctness[%s]: %.2f%% -> %.2f%%" % (label, before, after))
        # ИНВАРИАНТ линии «в»: A — это round-trip, он обязан быть РОВНО 100%.
        # Сравнения «не упала» мало: baseline с A<100 сделал бы порчу значения
        # неотличимой от нормы (см. docstring, линия «в»).
        if ac < 100.0 - 1e-9:
            regressions.append(
                "(в) masking_correctness[A round-trip] = %.2f%% < 100%% — "
                "значение НЕ восстанавливается байт-в-байт (инвариант проекта)" % ac
            )
        # C намеренно не роняет гейт — падение уходит в warnings у evaluate().

        # --- линии «в»/«г» ПО ТИПАМ МАСКИ, а не только по агрегату ---
        # ЗАЧЕМ (находка этапа S2). Агрегат B вырос 79.59% -> 86.79% на дельте
        # 2b -> HEAD, и это выглядело чистым улучшением. Разбор по типам показал,
        # что ОДНОВРЕМЕННО ADDRESS-B ОБВАЛИЛСЯ 75.33% -> 51.39%: агрегат вырос
        # потому, что у ADDRESS упало ЧИСЛО масок (5265 -> 1337), то есть упал его
        # вес в общем знаменателе, а PER/ORG подтянулись. Обвал целого типа
        # спрятался за ростом среднего — ровно тот же механизм, из-за которого
        # leak_v2 гейт с самого начала считает ПО КАЖДОМУ ТИПУ ОТДЕЛЬНО. Здесь то
        # же правило: падение границ одного типа не должно гаситься ростом другого.
        bt_ = baseline_agg.get("masking_correctness", {}).get("per_type", {})
        ct_ = current_agg.get("masking_correctness", {}).get("per_type", {})
        for t in ML.ALL_ENTITY_TYPES:
            sb, sc = bt_.get(t), ct_.get(t)
            if not sb or not sc or not sb["n_scored"] or not sc["n_scored"]:
                continue
            (a_b, b_b, _), (a_c, b_c2, _) = ML.mc_rates(sb), ML.mc_rates(sc)
            if a_c < a_b - masking_a_pp - 1e-9:
                regressions.append(
                    "(в) masking_correctness[A round-trip / %s]: %.2f%% -> %.2f%% — "
                    "round-trip УПАЛ на типе (допуск %.2fпп)" % (t, a_b, a_c, masking_a_pp))
            if b_c2 < b_b - masking_b_pp - 1e-9:
                regressions.append(
                    "(г) masking_correctness[B границы / %s]: %.2f%% -> %.2f%% — "
                    "границы масок типа УПАЛИ (допуск %.2fпп; агрегат мог вырасти "
                    "за счёт других типов)" % (t, b_b, b_c2, masking_b_pp))

    return rows, regressions, improvements


# --------------------------------------------------------------------------- #
#   Линия «а» (precision) + известный долг + диагностика состава (из этапа D)  #
# --------------------------------------------------------------------------- #
def missed_leak_ids(results):
    """Множество идентификаторов утечки-ДОЛГА: ADDRESS-сущность, НЕ детектированная
    (found=False) И утекающая (leak_v2!=none). Идентичность — (doc_id, value), та же,
    что в docs/known_leaks_stage_c.json (value == e['text']). Это ровно тот класс,
    что закрывается расширением мультиспана; found-but-partial-leak
    (датолайн-артефакт) СЮДА НЕ входит — он не про потерю детекции.
    Перенесено из experiments/stage_d/gate_d.py::_missed_leak_ids без изменения
    семантики (этап S2, сведение двух гейтов в один)."""
    ids = set()
    for r in results:
        if r.get("outcome") != "processed":
            continue
        for e in r["entities"]:
            if (e["type"] == "ADDRESS" and not e["found"]
                    and e["leak_v2"]["status"] != "none"):
                ids.add((r["doc_id"], e["text"]))
    return ids


def compare_precision(baseline_prec, current_prec, tol_pp):
    """Линия «а». Возвращает (regressions, improvements) по ВСЕМ типам эталона.

    ЭТАП GATE-2: линия расширена с NER-хребта (ORG/PER/ADDRESS) на все типы, у
    которых precision вообще определён (есть знаменатель tp+fp_neg в ОБОИХ
    прогонах). До этого реквизиты — оба ИНН, ОГРН, КПП, счета, БИК, телефон,
    e-mail, паспорт, СНИЛС, дата рождения, суммы — не охранялись НИЧЕМ: их
    точность могла упасть до нуля, и гейт остался бы зелёным. Тип, у которого
    precision is None (масок-решений по типу нет вовсе), пропускается — как и
    раньше, «нет данных» не приравнивается ни к 100%, ни к регрессу."""
    regressions, improvements = [], []
    for t in ML.ALL_ENTITY_TYPES:
        base = baseline_prec["per_type"].get(t, {}).get("precision")
        cur = current_prec["per_type"].get(t, {}).get("precision")
        if base is None or cur is None:
            continue
        if cur < base - tol_pp - 1e-9:
            regressions.append(
                "(а) precision[%s]: %.2f%% -> %.2f%% — упал ниже baseline − %.2fпп"
                % (t, base, cur, tol_pp))
        elif cur > base + 1e-9:
            improvements.append("precision[%s]: %.2f%% -> %.2f%%" % (t, base, cur))
    return regressions, improvements


# --------------------------------------------------------------------------- #
#   Линия «д» (порча документа: маски, не легшие ни на что) — этап GATE-2      #
# --------------------------------------------------------------------------- #
def compare_overmask(baseline_prec, current_prec, tol,
                     check_ledger=False, ledger_latest=None):
    """Линия «д». Маска, не пересёкшая НИ эталонную сущность (любого типа), НИ
    объявленный негатив, — это закрытый маской кусок обычного текста: номер
    пункта договора, слово прозы, обрывок таблицы. Гейт такие маски считал
    (диагностический `nothing` этапа D) и ПЕЧАТАЛ, но ни с чем не сравнивал —
    то есть порча документа проходила зелёной в любом объёме.

    Считается ПО ТИПАМ и по агрегату, по тому же правилу «рост одного типа не
    должен гаситься падением другого», что и leak_v2/masking.

    Возвращает (regressions, improvements)."""
    regressions, improvements = [], []

    # ЛОВУШКА НА ОБХОД (догонка этапа GATE-2, решение владельца). Линия «д»
    # блокирующая, а у блокирующей линии есть известный способ выродиться:
    # «опять красная — поднимем планку и поедем». Поэтому уровень линии живёт
    # НЕ ТОЛЬКО в дампе: рядом лежит журнал обоснований, куда пишет
    # promote_baseline.py. Если числа точки отсчёта разошлись с последней
    # записью журнала — планку двигали руками, мимо промотера, то есть БЕЗ
    # обоснования. Это красный гейт, а не примечание.
    # Сверка включается ТОЛЬКО для настоящего прогона (main передаёт
    # check_ledger=True). evaluate() — чистая функция, её зовут само-тесты на
    # синтетических дампах, у которых своих чисел порчи нет и сверять их с
    # журналом реального корпуса бессмысленно.
    if check_ledger:
        regressions += [
            "(д) %s" % p
            for p in OL.check_against_baseline(
                OL.overmask_numbers(baseline_prec), _latest=ledger_latest)
        ]

    for t in ML.ALL_ENTITY_TYPES:
        b = (baseline_prec["per_type"].get(t) or {}).get("nothing", 0)
        c = (current_prec["per_type"].get(t) or {}).get("nothing", 0)
        if c > b + tol:
            regressions.append(
                "(д) over-mask прозы[%s]: %d -> %d (+%d масок) — система закрыла "
                "маской текст, который не является ни эталонной сущностью, ни "
                "объявленным негативом (порча документа)" % (t, b, c, c - b))
        elif c < b:
            improvements.append("over-mask прозы[%s]: %d -> %d (-%d)" % (t, b, c, b - c))
    b_tot, c_tot = baseline_prec["nothing_total"], current_prec["nothing_total"]
    if c_tot > b_tot + tol:
        regressions.append(
            "(д) over-mask прозы[ВСЕГО]: %d -> %d (+%d масок)" % (b_tot, c_tot, c_tot - b_tot))
    elif c_tot < b_tot:
        improvements.append("over-mask прозы[ВСЕГО]: %d -> %d (-%d)"
                            % (b_tot, c_tot, b_tot - c_tot))
    return regressions, improvements


# --------------------------------------------------------------------------- #
#   Линия «е» (границы по НАПРАВЛЕНИЮ ошибки) — этап GATE-2                    #
# --------------------------------------------------------------------------- #
#: Что считаем и как называем в человеческом тексте регресса.
_BND_METRICS = (
    ("shorter", "недобор, сущностей", "under"),
    ("under_chars", "недобор, символов", "under"),
    ("longer", "перебор, сущностей", "over"),
    ("over_chars", "перебор, символов", "over"),
)


def compare_boundaries(baseline_bnd, current_bnd, tol_under, tol_over):
    """Линия «е». Две цены раздельно, по каждому типу и по агрегату:

      НЕДОБОР — маска КОРОЧЕ эталона: часть значения осталась открытым текстом.
                Это утечка ПДн.
      ПЕРЕБОР — маска ДЛИННЕЕ эталона: закрыт лишний текст. Данные защищены,
                документ хуже читается.

    masking B (линия «г») видит только первое: маска шире эталона по B не
    нарушение ВООБЩЕ (measure_lib.mc_check_bc), поэтому перебор мог расти
    неограниченно при зелёном гейте. Отсюда отдельная линия.

    Возвращает (regressions, improvements). Если поля нет в baseline или в
    текущем прогоне — это РЕГРЕСС, а не молчание: линия, которой нечем мерить,
    обязана сказать об этом вслух (тот же приём, что у masking в compare())."""
    regressions, improvements = [], []
    if not current_bnd["n_with_field"]:
        regressions.append(
            "(е) границы по направлению: в ТЕКУЩЕМ прогоне нет поля '%s' — дамп "
            "снят до появления линии; перегоните корпус текущим run_measurement.py"
            % ML.BND_FIELD)
        return regressions, improvements
    if not baseline_bnd["n_with_field"]:
        regressions.append(
            "(е) границы по направлению: в BASELINE нет поля '%s' — точка отсчёта "
            "снята до появления линии, сравнивать не с чем. Пересоберите "
            "results_baseline.json текущим run_measurement.py, иначе линия «е» слепа"
            % ML.BND_FIELD)
        return regressions, improvements

    for t in list(ML.ALL_ENTITY_TYPES) + ["ВСЕГО"]:
        if t == "ВСЕГО":
            b, c = baseline_bnd["total"], current_bnd["total"]
        else:
            b = baseline_bnd["per_type"].get(t) or ML._empty_bnd()
            c = current_bnd["per_type"].get(t) or ML._empty_bnd()
        for key, label, side in _BND_METRICS:
            tol = tol_under if side == "under" else tol_over
            if c[key] > b[key] + tol:
                regressions.append(
                    "(е) границы[%s / %s]: %d -> %d (+%d) — %s"
                    % (t, label, b[key], c[key], c[key] - b[key],
                       "часть эталона осталась ОТКРЫТЫМ текстом (утечка)"
                       if side == "under" else
                       "маска закрыла лишний текст (порча читаемости)"))
            elif c[key] < b[key]:
                improvements.append("границы[%s / %s]: %d -> %d (-%d)"
                                    % (t, label, b[key], c[key], b[key] - c[key]))
    return regressions, improvements


def compare_debt(baseline_results, current_results, known_leaks_ids, tol):
    """Условие 5. Возвращает (regressions, warnings, composition)."""
    base_set = missed_leak_ids(baseline_results)
    cur_set = missed_leak_ids(current_results)
    regressions, warnings = [], []
    if len(cur_set) > len(base_set) + tol:
        regressions.append(
            "(5) утечка-долг ADDRESS (found=False & leak): %d -> %d, допуск "
            "gate_config.MISSED_LEAK_TOLERANCE=+%d превышен"
            % (len(base_set), len(cur_set), tol))
    left = base_set - cur_set          # ушли из долга (перестали течь — обычно хорошо)
    joined = cur_set - base_set        # НОВЫЕ утечки (не в baseline-множестве)
    composition = {
        "base_count": len(base_set), "cur_count": len(cur_set),
        "left": sorted(left), "joined": sorted(joined),
        "joined_documented": sorted(j for j in joined if j in known_leaks_ids),
        "joined_undocumented": sorted(j for j in joined if j not in known_leaks_ids),
        "registry_not_leaking": sorted(known_leaks_ids - cur_set),
        "cur_not_in_registry": sorted(cur_set - known_leaks_ids),
    }
    if not regressions and (left or joined):
        warnings.append(
            "(5) состав утечки-долга сменился при том же/непревышенном счёте: "
            "−%d [ушли] +%d [пришли] (из пришедших НЕ в known_leaks: %d) — "
            "подмена «N на другой N», проверить вручную"
            % (len(left), len(joined), len(composition["joined_undocumented"])))
    return regressions, warnings, composition


def compare_suppression(current_results):
    """ЭТАП T4, линия «ж» — БЛОКИРУЮЩАЯ и АБСОЛЮТНАЯ (не дельта к baseline, в
    отличие от остальных линий): число эталонных сущностей, погашенных
    отрицательным классом (CLAUSE_REF/ROLE_TERM/COLLECTIVE — `_barrier_types`,
    `tokenizer._resolve_overlaps`), обязано быть РОВНО 0. Эталонная сущность
    под подавлением — дефект правила класса, а не цена этапа (ЗАДАНИЕ T4,
    «ГЛАВНЫЙ РИСК»): барьер, задевший реальную сущность, гасит её маску, и
    данные остаются в тексте открытыми — молча, агрегатный гейт иначе бы этого
    не заметил.

    Возвращает (regressions, suppressed_gold_all — поимённый список по всем
    документам, для печати и диагностики)."""
    suppressed_gold_all = []
    for r in current_results:
        if r.get("outcome") != "processed":
            continue
        for s in r.get("suppressed_gold", []):
            suppressed_gold_all.append({"doc_id": r["doc_id"], **s})

    regressions = []
    if suppressed_gold_all:
        names = ", ".join(
            "%s:%s[%s]<-%s" % (x["doc_id"], x["gold_type"], x["gold_text"][:30], x["suppressor_type"])
            for x in suppressed_gold_all[:10]
        )
        regressions.append(
            "(ж) ЗЕРКАЛО ПОДАВЛЕНИЯ: %d эталонных сущностей погашено отрицательным "
            "классом (допуск 0, БЕЗУСЛОВНО): %s%s"
            % (len(suppressed_gold_all), names,
               " …" if len(suppressed_gold_all) > 10 else "")
        )
    return regressions, suppressed_gold_all


def evaluate(baseline_results, current_results, known_leaks_ids, cfg=GC,
             check_ledger=False, ledger_latest=None):
    """ПОЛНАЯ оценка четырёх линий — чистая функция над двумя списками results.
    Ничего не печатает и не гоняет корпус: ровно эту функцию дёргает само-тест
    гейта (tests/corpus/test_gate_regression_detection.py), подкладывая
    синтетические регрессы в копию results.

    Возвращает dict:
      red          — bool, ронять ли прогон
      regressions  — список строк (каждая помечена линией «а»/«б»/«в»/«г» или условием)
      improvements — список строк
      warnings     — не роняющие сигналы (masking C, подмена состава долга)
      rows         — данные диф-таблицы по 14 типам
      composition  — состав утечки-долга
      base_agg/cur_agg, base_prec/cur_prec — сводки для печати
    """
    base_agg = ML.aggregate_results(baseline_results)
    cur_agg = ML.aggregate_results(current_results)
    base_prec = ML.precision_by_type(baseline_results)
    cur_prec = ML.precision_by_type(current_results)
    base_bnd = ML.boundaries_by_type(baseline_results)
    cur_bnd = ML.boundaries_by_type(current_results)

    rows, regressions, improvements = compare(
        base_agg, cur_agg,
        fp_tolerance=cfg.FP_TOLERANCE,
        leak_tolerance=cfg.LEAK_V2_TOLERANCE,
        masking_a_pp=cfg.MASKING_A_TOLERANCE_PP,
        masking_b_pp=cfg.MASKING_B_TOLERANCE_PP,
    )
    warnings = []

    # ЭТАП INSTR, ЧАСТЬ 2. Отказ (outcome == "refused") — не поломка (см.
    # docstring aggregate_results и комментарий в compare()), поэтому сам по
    # себе гейт не роняет, но обязан быть ВИДЕН отдельной строкой — иначе рост
    # отказов растворяется молча в "0 крешей" ровно так же, как раньше
    # растворялся в общем "outcome != processed".
    if len(cur_agg["refused"]) > len(base_agg["refused"]):
        new_refused = sorted(set(cur_agg["refused"]) - set(base_agg["refused"]))
        warnings.append(
            "ОТКАЗАНО (не креш): %d документ(ов) отклонены политикой непрочитанных "
            "зон (было %d): %s"
            % (len(cur_agg["refused"]), len(base_agg["refused"]),
               ", ".join(new_refused[:20]) + (" …" if len(new_refused) > 20 else ""))
        )
    elif len(cur_agg["refused"]) < len(base_agg["refused"]):
        improvements.append(
            "ОТКАЗАНО (не креш): %d -> %d" % (len(base_agg["refused"]), len(cur_agg["refused"])))

    p_reg, p_imp = compare_precision(base_prec, cur_prec, cfg.PRECISION_TOLERANCE_PP)
    regressions += p_reg
    improvements += p_imp

    o_reg, o_imp = compare_overmask(base_prec, cur_prec, cfg.OVERMASK_NOTHING_TOLERANCE,
                                    check_ledger=check_ledger, ledger_latest=ledger_latest)
    regressions += o_reg
    improvements += o_imp

    e_reg, e_imp = compare_boundaries(
        base_bnd, cur_bnd, cfg.BOUNDARY_UNDER_TOLERANCE, cfg.BOUNDARY_OVER_TOLERANCE)
    regressions += e_reg
    improvements += e_imp

    d_reg, d_warn, composition = compare_debt(
        baseline_results, current_results, known_leaks_ids, cfg.MISSED_LEAK_TOLERANCE)
    regressions += d_reg
    warnings += d_warn

    j_reg, suppressed_gold_all = compare_suppression(current_results)
    regressions += j_reg

    # masking C — мягкий уровень (решение владельца, STATE §6): предупреждение
    mb = base_agg.get("masking_correctness", {}).get("total")
    mc = cur_agg.get("masking_correctness", {}).get("total")
    if mb and mb["n_masks"] and mc and mc["n_masks"]:
        cb = ML.mc_rates(mb)[2]
        cc = ML.mc_rates(mc)[2]
        if cc < cb - cfg.MASKING_C_WARN_PP - 1e-9:
            warnings.append(
                "masking_correctness[C тип]: %.2f%% -> %.2f%% — упал. МЯГКИЙ "
                "уровень (решение владельца, STATE §6): гейт не роняю, но тип "
                "маски стал чаще расходиться с эталоном" % (cb, cc))

    return {
        "red": bool(regressions), "regressions": regressions,
        "improvements": improvements, "warnings": warnings,
        "rows": rows, "composition": composition,
        "base_agg": base_agg, "cur_agg": cur_agg,
        "base_prec": base_prec, "cur_prec": cur_prec,
        "base_bnd": base_bnd, "cur_bnd": cur_bnd,
        "suppressed_gold_all": suppressed_gold_all,
    }


# --------------------------------------------------------------------------- #
#                                  ПЕЧАТЬ                                      #
# --------------------------------------------------------------------------- #
def print_report(rows, baseline_agg, current_agg):
    header = "%-10s %6s | %16s | %20s | %20s | %14s" % (
        "тип", "n", "recall б->т", "leak_v2>=6 б->т", "leak_v2>=8 б->т", "FP-негат б->т")
    print(header)
    print("-" * len(header))
    for t, b, c in rows:
        n = c["n"] if c["n"] else b["n"]
        rb, rc = _rate(b["found"], b["n"]), _rate(c["found"], c["n"])
        l6b, l6c = _rate(b["leak_v2_6"], b["n"]), _rate(c["leak_v2_6"], c["n"])
        l8b, l8c = _rate(b["leak_v2_8"], b["n"]), _rate(c["leak_v2_8"], c["n"])
        fpb = baseline_agg["fp_on_neg"].get(t, 0)
        fpc = current_agg["fp_on_neg"].get(t, 0)
        m6 = _delta_mark(b["leak_v2_6"], c["leak_v2_6"])
        m8 = _delta_mark(b["leak_v2_8"], c["leak_v2_8"])
        mfp = _delta_mark(fpb, fpc)
        print("%-10s %6d | %6.1f%%->%6.1f%% | %s%6.1f%%->%6.1f%% | %s%6.1f%%->%6.1f%% | %s%5d->%-5d" % (
            t, n, rb, rc, m6, l6b, l6c, m8, l8b, l8c, mfp, fpb, fpc))

    print("-" * len(header))
    bt, ct = baseline_agg["total_bik_excl"], current_agg["total_bik_excl"]
    n = ct["n"] if ct["n"] else bt["n"]
    rb, rc = _rate(bt["found"], bt["n"]), _rate(ct["found"], ct["n"])
    l6b, l6c = _rate(bt["leak_v2_6"], bt["n"]), _rate(ct["leak_v2_6"], ct["n"])
    l8b, l8c = _rate(bt["leak_v2_8"], bt["n"]), _rate(ct["leak_v2_8"], ct["n"])
    m6 = _delta_mark(bt["leak_v2_6"], ct["leak_v2_6"])
    m8 = _delta_mark(bt["leak_v2_8"], ct["leak_v2_8"])
    fpb, fpc = baseline_agg["fp_on_neg_total"], current_agg["fp_on_neg_total"]
    mfp = _delta_mark(fpb, fpc)
    print("%-10s %6d | %6.1f%%->%6.1f%% | %s%6.1f%%->%6.1f%% | %s%6.1f%%->%6.1f%% | %s%5d->%-5d" % (
        "TOTAL*", n, rb, rc, m6, l6b, l6c, m8, l8b, l8c, mfp, fpb, fpc))
    print("* TOTAL — агрегат BIK-excl (см. BASELINE.md §1); FP-негат TOTAL — по ВСЕМ типам, условие 3 считает по этой строке.")
    print("  recall показан для контекста, регресс-условием НЕ является (см. docstring этого файла).")


def print_precision(base_prec, cur_prec, tol_pp):
    print("\n(а) PRECISION — FP = маска на ОБЪЯВЛЕННОМ негативе (единая ось этапа D)")
    print("  %-10s | %14s | %16s | %8s %8s" % (
        "тип", "precision б->т", "tp / fp_neg б->т", "cross", "nothing"))
    for t in ML.ALL_ENTITY_TYPES:
        db = base_prec["per_type"].get(t) or {}
        dc = cur_prec["per_type"].get(t) or {}
        if not dc:
            continue
        f = lambda d: ("%.2f%%" % d["precision"]) if d.get("precision") is not None else "n/a"
        # ЭТАП GATE-2: под линией «а» теперь ВСЕ типы, поэтому помечается не
        # «хребет», а факт охраны; NER-хребет назван отдельно как исторический
        # состав линии (по нему сравнимы цифры прошлых этапов).
        guarded = (db.get("precision") is not None and dc.get("precision") is not None)
        star = ("  <линия «а», допуск %.2fпп%s" % (
            tol_pp, "; NER-хребет" if t in ML.NER_SPINE_TYPES else "")) if guarded else \
            "  <precision не определён (нет масок-решений) — вне линии «а»"
        print("  %-10s | %6s->%-6s | %4d->%-4d %4d->%-4d | %8d %8d%s" % (
            t, f(db), f(dc), db.get("tp", 0), dc.get("tp", 0),
            db.get("fp_neg", 0), dc.get("fp_neg", 0),
            dc.get("cross", 0), dc.get("nothing", 0), star))
    print("  over-mask прозы (on-nothing): %d -> %d   cross-type (домен masking C): %d -> %d"
          % (base_prec["nothing_total"], cur_prec["nothing_total"],
             base_prec["cross_total"], cur_prec["cross_total"]))
    print("  — cross/nothing ДИАГНОСТИКА, в precision и в гейт НЕ входят (этап D).")


def print_masking_correctness(baseline_agg, current_agg):
    """Отчёт по masking_correctness РЯДОМ с recall/leak. Знаменатели печатаются
    явно: у A это все маски, у B/C — маски, легшие хоть на одну эталонную
    сущность (маске-ложняку покрывать нечего). Их нельзя читать как проценты от
    gold — это другая метрика (см. measure_lib, блок masking_correctness)."""
    mb = baseline_agg.get("masking_correctness", {}).get("total") or ML._empty_mc()
    mc = current_agg.get("masking_correctness", {}).get("total") or ML._empty_mc()
    print("\n(в)/(г) masking_correctness — корректность ТОГО, ЧТО СИСТЕМА ЗАМАСКИРОВАЛА")
    print("  (знаменатель — маски системы, НЕ gold; A/B роняют гейт, C — мягкий уровень)")
    ab, bb, cb = ML.mc_rates(mb)
    ac, bc_, cc = ML.mc_rates(mc)
    print("  масок: %d -> %d   (из них легли на эталон: %d -> %d)"
          % (mb["n_masks"], mc["n_masks"], mb["n_scored"], mc["n_scored"]))
    for label, before, after, hard in (
        ("A round-trip (значение восстановлено байт-в-байт)", ab, ac, True),
        ("B границы  (эталон закрыт масками целиком)", bb, bc_, True),
        ("C тип      (тип маски == тип эталона)", cb, cc, False),
    ):
        mark = " " if abs(after - before) < 1e-9 else ("+" if after > before else "-")
        print("  %s %-52s %6.2f%% -> %6.2f%%%s"
              % (mark, label, before, after, "" if hard else "   [мягкий]"))
    ch_b = (100.0 * mb["a_channel_ok"] / mb["n_masks"]) if mb["n_masks"] else 100.0
    ch_c = (100.0 * mc["a_channel_ok"] / mc["n_masks"]) if mc["n_masks"] else 100.0
    print("    справочно A-канал (restored == plain на месте маски): "
          "%.2f%% -> %.2f%% — расхождение с A выше даёт схлопывание '\\n'->' ' "
          "в ячейке таблицы при сборке plain, само значение при этом цело." % (ch_b, ch_c))


def print_overmask(base_prec, cur_prec, tol):
    """Линия «д» отдельным блоком — не строкой-припиской к precision, как было
    до этапа GATE-2 (тогда число печаталось, но ни с чем не сравнивалось)."""
    print("\n(д) OVER-MASK ПРОЗЫ — маски, не легшие НИ на эталон, НИ на объявленный негатив")
    print("  (порча документа: закрыт обычный текст. Допуск +%d по каждому типу и по агрегату)" % tol)
    for t in ML.ALL_ENTITY_TYPES:
        b = (base_prec["per_type"].get(t) or {}).get("nothing", 0)
        c = (cur_prec["per_type"].get(t) or {}).get("nothing", 0)
        if not b and not c:
            continue
        print("  %s %-10s %6d -> %-6d" % (_delta_mark(b, c), t, b, c))
    print("  %s %-10s %6d -> %-6d"
          % (_delta_mark(base_prec["nothing_total"], cur_prec["nothing_total"]),
             "ВСЕГО", base_prec["nothing_total"], cur_prec["nothing_total"]))
    # Число уровня НИКОГДА не печатается без своего обоснования: иначе через
    # полгода «330» — это просто число неизвестного происхождения.
    last = OL.latest()
    if last is None:
        print("  ⚠ ЖУРНАЛ ОБОСНОВАНИЙ ПУСТ — уровень ничем не объяснён (%s)"
              % os.path.basename(OL.LEDGER))
    else:
        print("  уровень принят: %s, %s (%s)" % (last["date"], last["author"], last["kind"]))
        print("  обоснование: %s" % last["reason"])
        if last.get("evidence"):
            print("  подтверждение: %s" % last["evidence"])
        print("  история движений: %d запис(ь/и) в %s — двигать уровень можно только "
              "через promote_baseline.py" % (len(OL.entries()), os.path.basename(OL.LEDGER)))


def print_boundaries(base_bnd, cur_bnd):
    """Линия «е»: две цены раздельно. Печатается ВСЕГДА, в том числе когда
    сравнивать не с чем, — число текущего прогона видно и в этом случае."""
    print("\n(е) ГРАНИЦЫ ПО НАПРАВЛЕНИЮ ОШИБКИ — «короче эталона» и «длиннее эталона» раздельно")
    print("  (недобор = часть значения открыта, УТЕЧКА; перебор = закрыт лишний текст, ПОРЧА)")
    if not cur_bnd["n_with_field"]:
        print("  НЕТ ДАННЫХ: в прогоне нет поля '%s' (дамп старше линии)." % ML.BND_FIELD)
        return
    if not base_bnd["n_with_field"]:
        print("  BASELINE БЕЗ ПОЛЯ '%s' — сравнивать не с чем, ниже только текущие числа."
              % ML.BND_FIELD)
    head = "  %-10s | %13s | %13s | %13s | %13s" % (
        "тип", "недобор шт", "недобор симв", "перебор шт", "перебор симв")
    print(head)
    print("  " + "-" * (len(head) - 2))
    rows = list(ML.ALL_ENTITY_TYPES) + ["ВСЕГО"]
    for t in rows:
        if t == "ВСЕГО":
            b, c = base_bnd["total"], cur_bnd["total"]
        else:
            b = base_bnd["per_type"].get(t) or ML._empty_bnd()
            c = cur_bnd["per_type"].get(t) or ML._empty_bnd()
        if not c["n"] and not b["n"]:
            continue
        cells = []
        for key, _label, _side in _BND_METRICS:
            cells.append("%s%5d->%-5d" % (_delta_mark(b[key], c[key]), b[key], c[key]))
        print("  %-10s | %13s | %13s | %13s | %13s"
              % (t, cells[0], cells[1], cells[2], cells[3]))
    tot, tob = cur_bnd["total"], base_bnd["total"]
    print("  сущностей с измеренной границей: %d -> %d; маска своего типа приписана: "
          "%d -> %d; ровно по эталону: %d -> %d"
          % (tob["n"], tot["n"], tob["assigned"], tot["assigned"], tob["exact"], tot["exact"]))
    print("  КОНТЕКСТ (в линию «е» НЕ входит, это домен recall/утечки): без маски своего "
          "типа %d -> %d сущностей, открыто символов %d -> %d"
          % (tob["not_found"], tot["not_found"], tob["not_found_chars"], tot["not_found_chars"]))


def print_debt(composition, kl_count):
    c = composition
    print("\n(5) ИЗВЕСТНЫЙ ДОЛГ — ADDRESS found=False & leak_v2 (реестр docs/known_leaks_stage_c.json)")
    print("  долг: %d -> %d  (допуск +%d; реестр документирует %d)"
          % (c["base_count"], c["cur_count"], GC.MISSED_LEAK_TOLERANCE, kl_count))
    print("  из текущего долга НЕ документировано реестром: %d" % len(c["cur_not_in_registry"]))
    print("  из реестра больше НЕ течёт: %d" % len(c["registry_not_leaking"]))
    if c["left"] or c["joined"]:
        print("  СОСТАВ ИЗМЕНИЛСЯ: ушли %d, пришли %d (из пришедших не в реестре: %d)"
              % (len(c["left"]), len(c["joined"]), len(c["joined_undocumented"])))
        for j in c["joined_undocumented"][:10]:
            print("     +НОВАЯ (не в known_leaks): %s" % (j,))


def print_suppression(current_results, suppressed_gold_all):
    """ЭТАП T4, линия «ж». Печатает и БЛОКИРУЮЩЕЕ число (эталон под подавлением,
    обязано быть 0), и ДИАГНОСТИЧЕСКОЕ (все случаи подавления, включая
    безопасные — подавлен не-эталонный текст, барьер отработал по назначению)."""
    total = 0
    by_pair: dict[tuple, int] = {}
    for r in current_results:
        if r.get("outcome") != "processed":
            continue
        for s in r.get("suppressions", []):
            if not s.get("seg_ok"):
                continue
            total += 1
            key = (s["suppressor_type"], s["suppressed_type"])
            by_pair[key] = by_pair.get(key, 0) + 1

    print("\n(ж) ЗЕРКАЛО ПОДАВЛЕНИЯ — отрицательный класс победил пересечение с "
          "маскируемым типом")
    print("  эталонных сущностей погашено (допуск 0, БЕЗУСЛОВНО): %d"
          % len(suppressed_gold_all))
    for x in suppressed_gold_all[:20]:
        print("     !! %s: %s %r <- %s [%d:%d]"
              % (x["doc_id"], x["gold_type"], x["gold_text"][:40],
                 x["suppressor_type"], x["sup_start"], x["sup_end"]))
    print("  всего случаев подавления (диагностика, безопасные включены): %d" % total)
    for (suppressor, suppressed), n in sorted(by_pair.items(), key=lambda kv: -kv[1]):
        print("     %s <- %s: %d" % (suppressed, suppressor, n))


def _known_leaks():
    kl = json.load(open(KNOWN_LEAKS, encoding="utf-8"))
    return set((r["doc_id"], r["value"]) for r in kl["leaks"]), len(kl["leaks"])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    results_path = None
    if argv and argv[0] == "--results":
        results_path = argv[1]
    # ЭТАП SPEED: --workers N — параллельный прогон по документам (по умолчанию
    # RM._default_workers(), т.е. авто; --workers 1 — прежнее однопоточное
    # поведение). См. run_measurement.run_all.
    workers = None
    if "--workers" in argv:
        workers = int(argv[argv.index("--workers") + 1])

    print("=== gate.py — ЕДИНЫЙ регресс-гейт (7 линий: precision / leak_v2 / masking A / "
          "masking B / over-mask прозы / границы по направлению / зеркало подавления) ===\n")

    ok_before, bad_before, n_checked = check_manifest()
    print(f"MANIFEST.sha256 (до прогона): {n_checked} файлов — {'OK' if ok_before else 'FAIL'}")
    if not ok_before:
        for p in bad_before[:20]:
            print("  !!", p)

    if not os.path.isfile(BASELINE):
        print(f"\nНет baseline {BASELINE} — гейту не с чем сравнивать.")
        return 2

    baseline_results = json.load(open(BASELINE, encoding="utf-8"))

    if results_path:
        print(f"\nОценка ГОТОВОГО прогона: {results_path} (корпус не гоняется; "
              "свежесть файла гейт проверить не может — отвечает запускающий)")
        current_results = json.load(open(results_path, encoding="utf-8"))
    else:
        gold = RM.load_gold()
        print(f"\nПрогон измерения: {len(gold)} документов (encrypt+decrypt, изолированное хранилище)…")
        current_results = RM.run_all(gold, verbose=True, workers=workers)
        # Отладочный снимок текущего прогона — НЕ results_baseline.json, точку
        # отсчёта гейт никогда не перезаписывает.
        json.dump(current_results, open(CURRENT_DUMP, "w", encoding="utf-8"), ensure_ascii=False)

    ok_after, bad_after, _ = check_manifest()
    print(f"\nMANIFEST.sha256 (после прогона): {'OK' if ok_after else 'FAIL'}")
    if not ok_after:
        for p in bad_after[:20]:
            print("  !!", p)

    kl_ids, kl_count = _known_leaks()
    # check_ledger=True — только здесь: это настоящий прогон против настоящей
    # точки отсчёта, и уровень линии «д» обязан совпадать с журналом обоснований.
    v = evaluate(baseline_results, current_results, kl_ids, check_ledger=True)

    # ЭТАП INSTR, ЧАСТЬ 2 — счётчики ВСЕГДА на виду, а не только при росте
    # относительно baseline (регресс/warning выше срабатывают лишь на дельте).
    print("\nисходы прогона: processed %d | КРЕШИ (условие 1, порог 0) %d | "
          "ОТКАЗАНО (не креш, не блокирует) %d"
          % (v["cur_agg"]["n_docs_processed"], len(v["cur_agg"]["crashed"]),
             len(v["cur_agg"]["refused"])))

    print()
    print_report(v["rows"], v["base_agg"], v["cur_agg"])
    print_precision(v["base_prec"], v["cur_prec"], GC.PRECISION_TOLERANCE_PP)
    print_masking_correctness(v["base_agg"], v["cur_agg"])
    print_overmask(v["base_prec"], v["cur_prec"], GC.OVERMASK_NOTHING_TOLERANCE)
    print_boundaries(v["base_bnd"], v["cur_bnd"])
    print_debt(v["composition"], kl_count)
    print_suppression(current_results, v["suppressed_gold_all"])

    regressions = list(v["regressions"])
    if not (ok_before and ok_after):
        regressions.insert(0, "(4) MANIFEST.sha256 не совпал (см. вывод выше) — корпус изменился, замер недействителен")

    print()
    if v["improvements"]:
        print(f"Улучшения ({len(v['improvements'])}):")
        for m in v["improvements"]:
            print("  + " + m)
    if v["warnings"]:
        print(f"\nПРЕДУПРЕЖДЕНИЯ ({len(v['warnings'])}) — не роняют гейт:")
        for m in v["warnings"]:
            print("  ⚠ " + m)
    if regressions:
        print(f"\nРЕГРЕССЫ ({len(regressions)}) — ГЕЙТ КРАСНЫЙ:")
        for m in regressions:
            print("  - " + m)
        return 1

    print("\nГейт ЗЕЛЁНЫЙ: регрессов не найдено по всем семи линиям "
          "(а precision по всем типам / б leak_v2 / в masking A / г masking B / "
          "д over-mask прозы / е границы по направлению / ж зеркало подавления) "
          "+ креши/FP/MANIFEST/долг.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
