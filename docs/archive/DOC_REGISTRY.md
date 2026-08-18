# DOC_REGISTRY — реестр решений по документам

Составлен: **2026-08-04**, этап **DOC** (наладка рабочего контура). Файл остаётся
навсегда: по нему видно, что и почему уехало из рабочего контура.

**Правило сессии: ничего не удалено физически.** Всё, что убрано из рабочего
контура, переехало в `docs/archive/` через `git mv` — содержимое не менялось
(единственное исключение — механическая правка путей в ссылках, см.
[DOC_REPORT.md](reports/DOC_REPORT.md)).

Решения:

- **ЖИВОЙ** — нужен в работе, остаётся на месте.
- **СЛИТЬ** — содержимое переехало в новый файл, оригинал уехал в архив.
- **В АРХИВ** — из рабочего контура убран, файл цел в `docs/archive/`.
  (Это и есть та строка задания, что раньше называлась «УДАЛИТЬ»: решение
  владельца от 2026-08-04 — не удалять физически ничего.)

Раскладка архива: `docs/archive/reports/` — отчёты этапов и аудиты (доказательная
база: цифры, репро, методики); `docs/archive/specs/` — спеки и материал будущих
этапов; `docs/archive/legal/` — лицензии и юридическая фактура; `docs/archive/` —
корневые документы, описывающие код.

---

## 1. Рабочий контур (после этапа DOC)

Четыре файла, больше в рабочем контуре быть не должно:

| Файл | Что в нём |
|---|---|
| [`CLAUDE.md`](../../CLAUDE.md) (корень) | Продукт, карта репозитория, команды, запреты, правила сессии. Потолок 150 строк |
| [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) | Кто за что отвечает и где используется; инварианты; отвергнутые решения |
| [`docs/STATE.md`](../STATE.md) | Живое состояние: этапы, зелёное/красное, что висит |
| [`docs/FINDINGS.md`](../FINDINGS.md) | Только открытые долги |

## 2. Верхний уровень `docs/` — решения по каждому файлу

| Файл | Строк | Решение | Куда уехал | Обоснование |
|---|---|---|---|---|
| `ARCHITECTURE.md` | 109 | **ЖИВОЙ** (переписан) | — | Рабочий контур. Прежняя редакция описывала инварианты до этапов A/B/C/D/E; переписана по коду на HEAD, вобрала `CODEMAP.md` и `DECISIONS.md` |
| `STATE.md` | 779 | **ЖИВОЙ** (переписан с нуля) | — | Рабочий контур. Прежняя редакция заявляла себя «≤200 строк», фактически 779; шапка «актуализирован 2026-07-19» отставала от содержимого на 3 коммита; §0 показывала прогон на HEAD `abf3ee8` как текущий |
| `FINDINGS.md` | 54 | **ЖИВОЙ** (переписан) | — | Рабочий контур. Нарушал собственную политику «только не закрытое» (6 строк `ЗАКРЫТО`), id `Eprime-A` стоял на трёх разных находках |
| `known_leaks_stage_c.json` | — | **ЖИВОЙ** | — | **Не документ:** реестр известного долга ADDRESS, читается гейтом (`tests/corpus/gate.py:54,277,737`). Трогать нельзя |
| `PRODUCT_LEGAL.md` | 452 | **ЖИВОЙ** | — | Объявлен неприкасаемым: материал к юристу, живая внешняя зависимость |
| `NN_HISTORY.md` | 288 | **ЖИВОЙ** | — | Объявлен неприкасаемым: вход в отложенное решение по второй нейросети |
| `CODEMAP.md` | 96 | **СЛИТЬ** | `archive/reports/` | Карта модулей целиком вошла в `ARCHITECTURE.md` §«Модули». Отдельный файл дублировал бы рабочий контур |
| `DECISIONS.md` | 65 | **СЛИТЬ** | `archive/reports/` | Отвергнутые решения вошли в `ARCHITECTURE.md` §«Отвергнутые решения» |
| `SNAPSHOT_2026-08-04.md` | 505 | **СЛИТЬ** | `archive/reports/` | Разведка S0 того же дня. Факты (7 линий гейта, состав корпусов, прогон 324 док.) перенесены в `STATE.md` и `ARCHITECTURE.md`; сам снимок — доказательная база с артефактами прогонов |
| `ARCHITECTURE_AUDIT.md` | 1032 | **В АРХИВ** | `archive/reports/` | Аудит сессии O4 на ветке `u1-desktop-packaging` (2026-07-25). Статус в шапке — «ОТЧЁТ», решения по §6 принимает владелец. Не рабочий контур |
| `PERF_REPORT.md` | 1967 | **В АРХИВ** | `archive/reports/` | Замер производительности и детерминизма (2026-07-27). Доказательная база; на него ссылается код подвыборки корпуса — ссылки починены |
| `ENTITY_SPEC.md` | 1239 | **В АРХИВ** | `archive/specs/` | Материал для решений по новым типам (T0, 2026-07-26). Вход будущих этапов T3, а не текущего контура; план этапов — §8.3 |
| `T0V_REPORT.md` | 958 | **В АРХИВ** | `archive/reports/` | Верификация предпосылок сессии T-GOLD, read-only отчёт |
| `LEGAL_CHECK.md` | 635 | **В АРХИВ** | `archive/legal/` | Фактическая проверка `PRODUCT_LEGAL.md` на дату 2026-07-28; сам `PRODUCT_LEGAL.md` остаётся живым |
| `SHIFRATOR_SPEC_FILE_DECRYPT.md` | 620 | **В АРХИВ** | `archive/specs/` | Инженерная спека компонента 2; компонент реализован полностью (шапка файла), спека — справочник по краевым случаям |
| `T2_REPORT.md` | 616 | **В АРХИВ** | `archive/reports/` | Отчёт этапа T2 (деньги/проценты/сроки), 2026-08-04 |
| `GENV_REPORT.md` | 602 | **В АРХИВ** | `archive/reports/` | Сравнение путей разметки (2026-07-27), read-only |
| `MARKUP_RULES.md` | 551 | **В АРХИВ** | `archive/specs/` | Правила разметки эталона для сессии T-GOLD; вход будущего этапа |
| `U5A_REPORT.md` | 546 | **В АРХИВ** | `archive/reports/` | Диагностика правки границ в интерфейсе; на него ссылается `app/core.py` (7 мест) — ссылки починены |
| `HANDOFF_SUBSET_ITER.md` | 477 | **В АРХИВ** | `archive/reports/` | Хендофф итерационного среза; сам себя помечает «УСТАРЕЛО ЧАСТИЧНО» в шапке |
| `ADDR_B_REPORT.md` | 470 | **В АРХИВ** | `archive/reports/` | Отчёт этапа ADDR-B (границы адресных масок) |
| `GATE2_REPORT.md` | 437 | **В АРХИВ** | `archive/reports/` | Отчёт этапа GATE-2. Описывает «шесть линий»/«15 типов» — верно на дату написания, устарело после линии «ж» (T4) и типов PERCENT/TERM (T2) |
| `T2_INN_REPORT.md` | 403 | **В АРХИВ** | `archive/reports/` | Отчёт этапа T2-INN (разделение ИНН на два типа) |
| `T_ARB_REPORT.md` | 359 | **В АРХИВ** | `archive/reports/` | Отчёт этапа T-ARB (арбитраж типов) |
| `WERT_CLEANUP.md` | 357 | **В АРХИВ** | `archive/reports/` | Протокол удаления реального документа из истории git (2026-07-28). Операция завершена |
| `T1_REPORT.md` | 364 | **В АРХИВ** | `archive/reports/` | Отчёт этапа T1 (переключатель типов) |
| `CORPUS_V2_REPORT.md` | 315 | **В АРХИВ** | `archive/reports/` | Отчёт построения корпуса v2. «Красная линия» (цифры на порождённом корпусе — верхняя граница) перенесена в `ARCHITECTURE.md` |
| `T1_UI_REPORT.md` | 300 | **В АРХИВ** | `archive/reports/` | Отчёт этапа T1-UI (экран выбора набора) |
| `T4_REPORT.md` | 269 | **В АРХИВ** | `archive/reports/` | Отчёт этапа T4 (отрицательные классы); на него ссылаются `entity_types.yaml` и тесты — ссылки починены |
| `LICENSES_AUDIT.md` | 220 | **В АРХИВ** | `archive/legal/` | Аудит лицензий зависимостей (2026-07-23) |
| `REAL_DOCS_CHECK.md` | 173 | **В АРХИВ** | `archive/reports/` | Проверка следов реальных договоров; вердикт закрыт операцией `WERT_CLEANUP.md` |
| `U4_FIX_REPORT.md` | 156 | **В АРХИВ** | `archive/reports/` | Отчёт этапа U4-FIX (сторож отчёта) |
| `BUILD_REPORT.md` | 140 | **В АРХИВ** | `archive/reports/` | Отчёт пересборки поставки (метка `build-20260729`) |
| `THIRD_PARTY_LICENSES.md` | 77 | **В АРХИВ** | `archive/legal/` | Черновик уведомлений о сторонних компонентах; в поставку exe не входит (проверено: `packaging/shifrator.spec` не кладёт ни одного `.md`) |

## 3. Корневые `.md`

| Файл | Строк | Решение | Куда уехал | Обоснование |
|---|---|---|---|---|
| `CLAUDE.md` | — | **НОВЫЙ, ЖИВОЙ** | — | Рабочий контур; в корне его не было вовсе |
| `README.md` | 60 | **ЖИВОЙ** | — | Вход в репозиторий для человека: что за продукт, как запустить CLI |
| `README_USER.md` | 149 | **ЖИВОЙ** | — | Инструкция конечного пользователя, поставляется вместе с папкой `SHIFRATOR` |
| `TECH_AUDIT.md` | 588 | **В АРХИВ** | `docs/archive/` | Техническое описание устройства на 2026-07-29; суть (конвейер, слои детекции, хранение) — в `ARCHITECTURE.md`. Решение владельца: не удалять |
| `ANCHOR_REGISTRY.md` | 144 | **В АРХИВ** | `docs/archive/` | Построчный разбор `src/anchor_registry.py` на HEAD `26d39ec` — глубже уровня рабочего контура; суть (якорь → реестр → проход 2) — в `ARCHITECTURE.md`. Решение владельца: не удалять |

## 4. `experiments/` — ТОЛЬКО ОПИСЬ, ничего не тронуто

Решение по всему каталогу: **отдельная сессия**. Здесь лежит живой код (репро
недетерминизма, хеш корпуса, отставной гейт) вперемешку с логами прогонов; разбор
требует запуска, а это другая зона. Ни один файл в этой сессии не перемещён и не
изменён.

Столбец «рабочее» — скрипт, который можно запустить и получить результат;
«лог/дамп» — артефакт конкретного прогона.

| Каталог | Трекнуто | Рабочие скрипты | Логи/дампы |
|---|---|---|---|
| `a0_gliner/` | 43 | `00_smoke.py`, `01_prepare_input.py`, `02_run_gliner.py`, `03_analyze.py`, `04_prepare_synthetic.py`, `05_run_gliner_synthetic.py`, `06_analyze_synthetic.py`, `07_emit_tables.py` | 20 `gliner_*_th*.json` (прогоны по порогам), таблицы. venv и кеш модели не трекаются (`.gitignore`) |
| `stage_a_prime/` | 3 | нет | 3 дампа `results_*.json` |
| `stage_addr_b/` | 17 | `addr_causes.py`, `addr_probe.py`, `addr_report.py`, `neighbours.py`, `snip.py`, `update_known_leaks.py` | `gate_*.log`, `probe_*.json/log`, `pytest_full.log`, `results_before_addrb.json` |
| `stage_b/` | 3 | `compare.py`, `run.py` | — |
| `stage_build/` | 2 | `exe_live_check.py` | `exe_live_check.log` |
| `stage_c/` | 18 | `agency_dump.py`, `build_known_leaks.py`, `compare.py`, `dbg.py`, `dump.py`, `measure_sub.py` | `dump_*.txt`, `gate_report*.txt`, `measure_*.txt`, `pytest_*.txt`, `.txt`-выдержки |
| `stage_c_prime/` | 2 | `run_cprime.py` | `results_d_cprime.json` |
| `stage_d/` | 7 | **`gate_d.py` — ОТСТАВНОЙ гейт**: при запуске сам отказывается (`RETIRED_REFUSAL`, exit 2); `run_head.py` | `baseline_d.json`, `gate_d_report.txt`, `precision_table.txt`, `run_head.log` |
| `stage_e/` | 5 | `run_e.py`, `verify_1a_1b.py` | `results_d_e.json`, `verify_1a1b_result.json`, `verify_full.log` |
| `stage_e_prime/` | 8 | `run_eprime.py`, `update_known_leaks.py` | `debt_final.json`, `gate_*.txt`, `results_d_eprime.json`, `verify_*` |
| `stage_eprime_determinism/` | 8 | **`owner_repro.py`** (послойная L1–L4 диагностика недетерминизма — вход открытой находки), **`corpus_anon_sha.py`** (агрегатный хеш корпуса), `make_many_styles_fixture.py`, `make_synthetic_large.py`, `probe_capsresolver_id.py`, `probe_natasha.py`, `repro.py` | `_corpus_sha_new.json` |
| `stage_gate2/` | 5 | нет | `gate_*.log`, `pytest_*.log`, `results_baseline_before_gate2.json` |
| `stage_o2/` | 11 | `ab_bench.py`, `acceptance.py`, `etalon.py`, `probe_*.py` (7 шт.), `profile_run.py` | порождаемые ими `_*.json`, `_*.log`, `*.pstats` не трекаются (`.gitignore:68-70`) |
| `stage_s3/` | 8 | `overmask_shape.py`, `probe.py`, `probe_b3.py`, `probe_view.py`, `run_dump.py`, `table.py` | `gate_after.txt`, `results_eprime_head.json` |
| `stage_t1/` | 7 | `compare.py`, `sweep.py` | `ACCEPTANCE.txt`, `after.json`, `before.json`, `gate.log`, `pytest_full.log` |
| `stage_t2_inn/` | 10 | `compare_split.py` | `dump_before_after.txt`, `gate_*.log`, прочие дампы |
| `stage_tarb/` | 2 | `diff_masks.py`, `dump_masks.py` | — |
| `per_spread/` | 17 | `RECON.md` (разведка с адресами ДО правки), `surn_slot_survey.py` (проверка правила СЛОТА ФАМИЛИИ до кода), `keys_diff.py` (сличение реестров якорей до/после — поймал два дефекта), `compare.py` (таблица ДО/ПОСЛЕ), `mc_b.py` (линия «г» в абсолютных числах), `diag_per.py`, `probe_doc.py`, `probe2.py`, `probe_orig.py`, `segdump.py`, `leakctx.py`, `patch1..5.py` + `patch_docs/journal/state.py` (правки текстом; `patch4.py` — ИЗМЕРЕННЫЙ ТУПИК, откачен) | `gate_per_spread.log`, `accept_circle.log`, `compare.txt`, `mc_b.txt` |
| (корень) | 1 | нет | `stage_t1_ui_gate.log` |

## 5. Что НЕ трогалось этой сессией

`tests/corpus/README.md`, `tests/corpus_v2/README.md` (второй несёт сторож
TRANCHE), `tests/corpus/BASELINES.md`, `tests/corpus/GENERATION_NOTES.md`,
`tests/corpus/overmask_ledger.json`, `docs/known_leaks_stage_c.json`,
`bench/README.md`, `app/README.md`, всё в `experiments/`, весь `docs/archive/`
до этой сессии (кроме починки путей в ссылках), `src/`, `tests/`, корпуса,
эталоны, манифесты.
