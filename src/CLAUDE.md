# Зона детекции

**Стена: детектор не смотрит в ответы.** Не читать и не импортировать
`tests/corpus_v2/values.py`, `tests/corpus_v2/generate.py`, `gold_v2.json`,
`tests/corpus/gold.json`. Полное правило и цена нарушения —
[`.claude/rules/detection-wall.md`](../.claude/rules/detection-wall.md).

Порядок слоёв детекции живёт в ОДНОМ месте — `pipeline.run_detection`. Копий
порядка нет и заводить нельзя: раньше их было три, и интерфейс однажды отстал
на целый этап.

Новый маскируемый тип обязан появиться в `_REGEX_PRIORITY` или `_NER_PRIORITY`
(`tokenizer.py`), иначе `assert_priority_contract` уронит сборку — забытый тип
молча проигрывает каждое пересечение.

Инварианты, которые нельзя ломать, — [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §8.
