# SHIFRATOR — рабочий контур

## Продукт

Локальный офлайн-инструмент (Python 3.11, Windows). Находит в договоре ПДн и
реквизиты, заменяет обратимыми токенами `[TYPE_N]` **перед** ручной отправкой
текста в стороннюю LLM, потом восстанавливает исходные значения в ответе — в
тексте и в принесённом `.docx/.xlsx/.pptx` с сохранением оформления.
Главный риск системы — **тихая утечка ПДн**: пропущенная сущность уходит в LLM
без предупреждения. Любое изменение детекции оценивается сначала по этому риску.

## Карта репозитория

| Где | Что |
|---|---|
| `src/` | Библиотека: детекция, маскирование, восстановление. Плоские импорты (`from tokenizer import …`), `src/` — не пакет |
| `shifrator.py` | CLI: `encrypt` / `decrypt` / `decrypt-file` / `delete` |
| `entity_types.yaml` | СОБРАННЫЙ артефакт (руками не править!): `tests/corpus_v2/assemble_types.py` из `entity_types.base.yaml` + `tests/corpus_v2/typedefs/*.yaml`. Порядок арбитража — ключ `arbitration_order` там же. Новый тип — одним файлом-описанием: `docs/HOWTO_NEW_TYPE.md` |
| `app/` | Десктопный интерфейс: stdlib `http.server` + один `index.html`, только `127.0.0.1` |
| `tests/` | pytest; `tests/corpus/` — корпус v1 + гейт; `tests/corpus_v2/` — корпус v2 + генератор; `tests/component2/` — OOXML |
| `docs/` | Рабочий контур: `JOURNAL.md`, `STATE.md`, `ARCHITECTURE.md`, `FINDINGS.md`. Остальное — `docs/archive/`, реестр переездов — `docs/archive/DOC_REGISTRY.md` |
| `experiments/` | По-этапные разведочные скрипты и логи прогонов. Опись — `docs/archive/DOC_REGISTRY.md` §4 |
| `bench/` | Легаси-харнесс регресса адреса. **Это не гейт** |
| `packaging/` | PyInstaller-спека и лок сборки |

Состояние сейчас — [`docs/STATE.md`](docs/STATE.md). История по этапам —
[`docs/JOURNAL.md`](docs/JOURNAL.md). Устройство — [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Открытые долги — [`docs/FINDINGS.md`](docs/FINDINGS.md).

Правила зон лежат по месту: [`src/CLAUDE.md`](src/CLAUDE.md),
[`tests/corpus/CLAUDE.md`](tests/corpus/CLAUDE.md),
[`tests/corpus_v2/CLAUDE.md`](tests/corpus_v2/CLAUDE.md).

## Команды

Рабочий интерпретатор — **только** `venv/Scripts/python.exe` (в нём стоит
`natasha`). Другой интерпретатор = ошибки сбора тестов.

```
venv/Scripts/python.exe -m pytest -q                  # набор тестов (~7-8 мин, xdist -n auto)
venv/Scripts/python.exe tests/corpus/subsample.py     # быстрый набор: 33 док., ~55 с
venv/Scripts/python.exe tests/corpus/gate.py          # ПОЛНЫЙ гейт: 324 док., ~9-10 мин
venv/Scripts/python.exe app/server.py                 # интерфейс на 127.0.0.1:8765
venv\Scripts\pyinstaller.exe packaging\shifrator.spec --distpath dist --workpath build
```

## Запуск по зоне

Только для итераций внутри сессии — **приёмка этапа всегда полный набор**
(`pytest -q` без файлового списка), ни одна зона от него не освобождена.

```
детекция:      venv/Scripts/python.exe -m pytest tests/test_regex_detector.py tests/test_ner_detector.py tests/test_normalizer.py tests/test_case_detection.py tests/test_stage3_detectors.py tests/test_stage_b_per.py tests/test_stage_c_prime_quotes.py tests/test_golden_addresses.py tests/test_addr_b_boundaries.py tests/test_negative_classes.py tests/test_t2_inn_split.py tests/test_t2_percent_term.py tests/test_determinism.py tests/test_breaking_leaks.py tests/test_breaking_redos.py tests/test_boundary_entities.py tests/test_extractor.py tests/test_adversarial_extractor.py tests/test_unread_zones.py tests/test_future_contracts.py
арбитраж:       venv/Scripts/python.exe -m pytest tests/test_arbitration_contract.py tests/test_adversarial_tokenizer.py tests/test_syntax_compound.py tests/test_stage_e_prime.py tests/test_stage_e_spans.py tests/test_stage_s3.py tests/test_wave2_fix.py tests/test_wave2_overcapture.py
маскирование:   venv/Scripts/python.exe -m pytest tests/test_tokenizer.py tests/test_detokenizer.py tests/test_adversarial_detokenizer.py tests/test_type_policy.py tests/test_integration.py tests/component2
гейт/метрики:   venv/Scripts/python.exe -m pytest tests/corpus/test_gate_regression_detection.py tests/corpus/test_overmask_ledger_guard.py tests/test_gate_d.py tests/test_leak_v2.py tests/test_masking_correctness.py tests/test_precision_metric.py tests/test_corpus_no_crash.py tests/test_subset_iter_coverage.py
корпуса:        venv/Scripts/python.exe -m pytest tests/test_corpus_v2_axis_coverage.py tests/test_corpus_v2_reproducible.py tests/test_corpus_v2_structure_groups.py tests/test_corpus_v2_value_tricks.py tests/test_gold_type_contract.py
интерфейс/хранение: venv/Scripts/python.exe -m pytest tests/test_cli.py tests/test_adversarial_cli.py tests/test_cli_unread_zones.py tests/test_breaking_session.py tests/test_session_store.py tests/test_adversarial_session_store.py tests/test_storage_s1.py tests/test_type_policy_ui.py tests/test_u3_markup.py tests/test_u4_report.py tests/test_wave1_verification.py
сборка:         venv/Scripts/python.exe -m pytest tests/test_u1_packaging.py
```

`test_wave1_verification.py` — сквозной (CLI+хранение+файловое маскирование из
ранней волны верификации), приписан к интерфейсу/хранению по большинству
проверок; при правках маскирования/OOXML прогонять и его тоже.

## Запреты навсегда

1. **Не пушить.** Коммитить самому, `git push` — решение владельца.
2. **Корпус v1 заморожен.** `tests/corpus/docs/**` и `tests/corpus/gold.json` не
   редактировать, не удалять, не дополнять. `sha256sum -c MANIFEST.sha256` в
   `tests/corpus/` обязан быть OK до и после любой правки.
3. **Планку гейта молча не двигать.** `tests/corpus/results_baseline.json` и
   `MANIFEST.sha256` меняются ТОЛЬКО через `tests/corpus/promote_baseline.py`
   с `--author` и `--reason`; инструмент пишет журнал `overmask_ledger.json`.
   Допуски в `tests/corpus/gate_config.py` не ослаблять — все нулевые, и это
   измеренный факт, а не осторожность.
4. **Стена между детекцией и генератором корпуса v2.** Код в `src/` не читает и
   не импортирует `tests/corpus_v2/` (values, generate, typedefs, эталоны).
   Иначе детектор учится на ответах, и цифры перестают что-либо значить.
   С TYPE-FACTORY-2 стена механическая: AST-страж
   `tests/test_type_factory.py::TestWall` + белый список сборки.
5. **Красный тест не чинится ослаблением теста.** Падение — либо дефект в коде,
   либо честная находка в `FINDINGS.md`.
6. **Запрет детекции TRANCHE СНЯТ владельцем 2026-08-08** (TYPE-FACTORY-2:
   черновик — из живой документации, условие сторожа исполнено). Маскируется
   ТОЛЬКО именная ветвь (транш «Имя»); порядковая и описательные обороты —
   негативы `kind=ordinal-tranche`. Страж «TRANCHE с числом = ошибка» остаётся.
7. **Реальные документы не коммитить.** Публичный репозиторий, в истории уже была
   утечка договора. Дампы реальных текстов — никуда, включая `experiments/` и `docs/`.
8. **Путь и формат хранилища сессий не менять** (`~/.shifrator/`, `key.bin`,
   `{sid}.enc`): смена делает существующие сессии пользователя невосстановимыми.

## Правила сессии

- **Одна тема — одна зона.** Сессия про документы не правит детекторы; сессия про
  детекторы не правит корпус и эталон.
- **Не хватает данных, файл противоречит файлу, непонятно живой скрипт или
  мёртвый — спроси и остановись.** Не угадывать, не удалять «на всякий случай».
- **Не запускать процессы, которые не завершаются сами** — веб-сервер,
  наблюдение за файлами, интерактивные оболочки. Нужен сервер для проверки —
  поднимать в фоне с жёстким ограничением по времени, проверять запросом и
  сразу гасить (STORE: −1 ч 45 мин на поднятом интерфейсе).
- **Скрипты с многопроцессностью на Windows — только с защитой точки входа**
  (`if __name__ == "__main__"`): иначе воркеры `Pool` реимпортируют скрипт как
  `__main__` и он запускает сам себя (A6: −15 минут на зависший замер).
- **Новое рабочее дерево (`git worktree add`) — сразу junction на `venv` из
  основного дерева** (`mklink /J venv <root>\venv`): часть тестов зовёт
  `<root>/venv/Scripts/python.exe` подпроцессом и без него ложно падает
  (блок INSTR: третий такой случай подряд).
- **Любое утверждение о коде — гипотеза, пока нет `файл:строка` или прогона.**
  Цифры из отчётов прошлых этапов — тоже гипотеза: они верны на свою дату.
- Коммитить группами, чтобы можно было откатить точечно. Не пушить.

## Правило рождения документов

**Отчёт этапа отдельным файлом не создаётся.** Итог этапа = запись в
[`docs/JOURNAL.md`](docs/JOURNAL.md) (только дописывается снизу, удалять и
переписывать записи нельзя никогда) + перезапись [`docs/STATE.md`](docs/STATE.md)
целиком. Новые файлы в `docs/` — только с разрешения владельца.

Потолки размера, обязательные к соблюдению: `CLAUDE.md` ≤ 150 строк,
`docs/ARCHITECTURE.md` ≤ 400 строк. Упёрлись — сокращать или выносить в архив,
а не растить. `JOURNAL.md` потолка не имеет: он накопительный.

Ничего не удаляется физически — только `git mv` в `docs/archive/`.

## Правило прогона корпуса

Полный корпус (`gate.py`, 324 документа) гоняется **один раз, последним
действием** этапа, на чистом дереве — прогон стоит ~10 минут и перезаписывает
`results_gate_current.json`. Во время работы — быстрый набор (`subsample.py`,
33 документа), но он **не приёмка**: у него собственная точка отсчёта
`results_iter_baseline.json`, и его «регрессы» — находка, а не вердикт.

Если гейт покраснел — не двигать планку. Сначала понять, это регресс продукта
или сдвиг разметки масок (MASK-SHIFT: маски те же, но метрика их считает иначе).
Разница видна поимённым сравнением масок, а не агрегатом.
