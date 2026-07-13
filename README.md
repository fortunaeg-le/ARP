# SHIFRATOR

Локальный офлайн-инструмент на Python 3.11. Находит конфиденциальные данные (ПДн, реквизиты) в
тексте договора и заменяет их обратимыми токенами `[TYPE_N]` **перед** ручной отправкой текста в
стороннюю LLM, а затем восстанавливает исходные значения в ответе LLM — в тексте и в принесённом
файле `.docx/.xlsx/.pptx` с сохранением оформления.

## Как запустить

Рабочий интерпретатор — только `venv/Scripts/python.exe` (в нём стоит `natasha`). Из корня:

```
venv/Scripts/python.exe shifrator.py encrypt <файл.docx|.txt> [--config entity_types.yaml]
venv/Scripts/python.exe shifrator.py decrypt <session_id>        # ответ LLM — в stdin
venv/Scripts/python.exe shifrator.py delete  <session_id>
venv/Scripts/python.exe shifrator.py decrypt-file <session_id> <файл.docx|.xlsx|.pptx> [--out <путь>]
```

`encrypt` печатает в stdout **только** `session_id`; анонимизированный текст пишется в
`~/.shifrator/sessions/{session_id}.txt`. Вход `encrypt` — только `.docx` / `.txt`.

Тесты: `venv/Scripts/python.exe -m pytest -q` (эталон: 525 passed, 1 xfailed).

## Статус

Активной известной утечки ПДн нет: прежний блокер W2-D1 исправлен и закоммичен. Не начаты
компонент 1 (веб) и PDF-извлечение. Полный статус и открытые дефекты — [docs/STATE.md](docs/STATE.md).

## Куда идти дальше (порядок чтения)

**Новая сессия читает: [docs/STATE.md](docs/STATE.md).** При правке логики — плюс
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). При навигации по коду —
[docs/CODEMAP.md](docs/CODEMAP.md). Остальное читать не нужно.

- [docs/DECISIONS.md](docs/DECISIONS.md) — отвергнутые решения (перед «а давайте…»).
- [docs/SHIFRATOR_SPEC_FILE_DECRYPT.md](docs/SHIFRATOR_SPEC_FILE_DECRYPT.md) — глубокая спека
  компонента 2 (детокенизация файлов).
- [docs/reports/](docs/reports/) — исторические отчёты-первоисточники (доказательная база; не
  руководство, могут врать — см. `INDEX.md` там).
- [docs/archive/](docs/archive/) — устаревшие доки (см. `INDEX.md` там).
- `bench/` — регрессионный/замерный харнесс (`bench/README.md`).
