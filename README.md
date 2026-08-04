# SHIFRATOR

Локальный офлайн-инструмент на Python 3.11. Находит конфиденциальные данные (ПДн, реквизиты) в
тексте договора и заменяет их обратимыми токенами `[TYPE_N]` **перед** ручной отправкой текста в
стороннюю LLM, а затем восстанавливает исходные значения в ответе LLM — в тексте и в принесённом
файле `.docx/.xlsx/.pptx` с сохранением оформления.

## Как запустить

Рабочий интерпретатор — только `venv/Scripts/python.exe` (в нём стоит `natasha`). Из корня:

```
venv/Scripts/python.exe shifrator.py encrypt <файл.docx|.txt> [--config entity_types.yaml] [--allow-lossy]
venv/Scripts/python.exe shifrator.py decrypt <session_id>        # ответ LLM — в stdin
venv/Scripts/python.exe shifrator.py delete  <session_id>
venv/Scripts/python.exe shifrator.py decrypt-file <session_id> <файл.docx|.xlsx|.pptx> [--out <путь>]
```

`encrypt` печатает в stdout **только** `session_id`; анонимизированный текст пишется в
`~/.shifrator/sessions/{session_id}.txt`. Вход `encrypt` — только `.docx` / `.txt`.

**Непрочитанные зоны `.docx` (этап 1).** Система читает только тело документа. Колонтитулы,
сноски, надписи и вложенные таблицы она читать пока НЕ умеет — а раньше молча выбрасывала их
текст из результата. Теперь `encrypt` **по умолчанию отказывается** работать с таким
документом: `exit 2` и таблица «тип зоны / часть / сколько символов» в stdout. Молча потерять
кусок договора хуже, чем отказаться его обрабатывать.

Обойти отказ — `--allow-lossy` (или `strict_zones: false` в `entity_types.yaml`): тело
документа обработается, а текст непрочитанных зон запишется в
`~/.shifrator/sessions/{session_id}.unread.json`, чтобы было видно, что именно выброшено.
**Этот файл содержит ПДн открытым текстом** — наружу (в LLM) уходит только `{session_id}.txt`.
Подробности — `docs/archive/reports/HANDOFF_STAGE_1.md`.

## Три уровня прогона

| Уровень | Команда | Когда | Что проверяет |
|---|---|---|---|
| **fast** | `venv/Scripts/python.exe -m pytest -q` | каждый коммит | Юнит/интеграционные тесты блоков 1-12, включая `test_g_regression` (группа G, компонент 2 не ломает блоки 1-7) — по умолчанию, без `-m slow`. ~200с (`test_g_regression` сам поднимает fast-набор ещё раз отдельным subprocess'ом — см. `docs/archive/reports/HANDOFF_STAGE_0D.md`). |
| **slow** | `venv/Scripts/python.exe -m pytest -m slow -q` | перед merge | Полный корпус `tests/corpus/` без крешей (324 документа, `test_corpus_no_crash.py`). Минуты. |
| **measure** | `venv/Scripts/python.exe tests/corpus/gate.py` | CI на PR, трогающем `src/` | Регресс-гейт: encrypt+decrypt по всем 324 документам корпуса, сравнение с `tests/corpus/results_baseline.json` (крешей быть не должно, частичная утечка `leak_v2` по каждому из 13 типов не должна расти, FP по негативам — не больше `tests/corpus/gate_config.FP_TOLERANCE`, `MANIFEST.sha256` корпуса — OK). Минуты; **НЕ вешать на pre-commit** — CI на PR. См. `docs/archive/reports/HANDOFF_STAGE_0D.md`. |

Тесты: `venv/Scripts/python.exe -m pytest -q` (эталон на `d8e969b`: 769 passed, 1 deselected, 12 xfailed, ~350 c).

## Статус

Активной известной утечки ПДн нет: прежний блокер W2-D1 исправлен и закоммичен. Не начаты
компонент 1 (веб) и PDF-извлечение. Полный статус и открытые дефекты — [docs/STATE.md](docs/STATE.md).

## Куда идти дальше (порядок чтения)

**Рабочий контур — четыре файла, больше читать не нужно:**

- [CLAUDE.md](CLAUDE.md) — правила работы, команды, запреты навсегда.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — устройство: конвейер, слои детекции,
  арбитраж, гейт, корпуса, инварианты и отвергнутые решения.
- [docs/STATE.md](docs/STATE.md) — что сейчас зелёное, что красное, что висит.
- [docs/FINDINGS.md](docs/FINDINGS.md) — открытые долги.

Всё остальное — в [docs/archive/](docs/archive/); что и почему туда уехало,
записано в [docs/archive/DOC_REGISTRY.md](docs/archive/DOC_REGISTRY.md). Например:

- [docs/archive/specs/SHIFRATOR_SPEC_FILE_DECRYPT.md](docs/archive/specs/SHIFRATOR_SPEC_FILE_DECRYPT.md) — глубокая спека
  компонента 2 (детокенизация файлов).
- [docs/archive/](docs/archive/) — летопись: устаревшие доки и исторические отчёты-
  первоисточники (доказательная база; не руководство, могут врать — см. `INDEX.md` там).
- `bench/` — регрессионный/замерный харнесс (`bench/README.md`).
