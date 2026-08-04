# Зона корпуса v1 и гейта

**Заморожено:** `docs/**` и `gold.json` не редактировать, не дополнять, не
удалять (заперто хуком). `sha256sum -c MANIFEST.sha256` — OK до и после.

**Планка двигается только `promote_baseline.py`** с `--author` и `--reason`;
прямая правка `results_baseline.json`/`MANIFEST.sha256` заперта хуком.
Допуски в `gate_config.py` не ослаблять — все нули измерены, а не выбраны.

Полное правило — [`.claude/rules/corpus-frozen.md`](../../.claude/rules/corpus-frozen.md).
Что мерит каждая из семи линий — [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) §5.
