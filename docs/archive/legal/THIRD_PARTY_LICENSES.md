# Third-party licenses / Уведомления о сторонних компонентах

Черновик. Требует юридической вычитки перед первой коммерческой поставкой —
см. [LICENSES_AUDIT.md](LICENSES_AUDIT.md) (пункт Ю1: трактовка CC BY-SA для
словаря OpenCorpora).

Этот продукт (SHIFRATOR) использует следующие сторонние open-source
компоненты. Полные тексты лицензий доступны по указанным ссылкам либо в
дистрибутивах соответствующих пакетов (`site-packages/<pkg>-*.dist-info/LICENSE`).

## MIT License

Копирайт принадлежит соответствующим авторам, полный текст:
https://opensource.org/license/mit/

- python-docx — Copyright (c) Steve Canny
- python-pptx — Copyright (c) Steve Canny
- PyYAML
- natasha — Copyright (c) 2016, natasha contributors — https://github.com/natasha/natasha
- navec — Copyright (c) 2017, natasha contributors — https://github.com/natasha/navec
- slovnet — https://github.com/natasha/slovnet
- razdel — https://github.com/natasha/razdel
- yargy — https://github.com/natasha/yargy
- ipymarkup — https://github.com/natasha/ipymarkup
- pymorphy2 — Copyright (c) Mikhail Korobov — https://github.com/kmike/pymorphy2
- pymorphy2-dicts-ru (код-обвязка; данные см. ниже отдельно) — https://github.com/kmike/pymorphy2-dicts
- DAWG-Python
- openpyxl
- et_xmlfile
- pillow (MIT-CMU / HPND-подобная — вариант MIT, доп. copyright: Secret Labs AB, Fredrik Lundh)
- typing_extensions (PSF-2.0 — пермиссивная лицензия Python Software Foundation)
- git-filter-repo (инструмент разработки, не распространяется с продуктом)

## BSD License (2-Clause / 3-Clause)

- lxml — https://lxml.de/
- xlsxwriter
- numpy (составная: BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 —
  см. NumPy NOTICE в дистрибутиве пакета для полного перечня встроенных
  компонентов и их атрибуций)
- pycparser
- cffi (MIT-0 — вариант MIT без обязательной атрибуции)

## Apache License 2.0 (либо дуальная Apache-2.0 OR BSD)

- cryptography — https://github.com/pyca/cryptography (Apache-2.0 OR BSD-3-Clause)
- intervaltree
- sortedcontainers
- packaging (Apache-2.0 OR BSD-2-Clause)

## Creative Commons Attribution-Share Alike 3.0 (данные, не код)

- **Словарь OpenCorpora**, используемый пакетом `pymorphy2-dicts-ru`
  (морфологические данные для `pymorphy2`) — источник: opencorpora.org,
  revision 417127. Лицензия: CC BY-SA 3.0 —
  https://creativecommons.org/licenses/by-sa/3.0/
  Атрибуция: данные предоставлены проектом OpenCorpora (opencorpora.org).
  **См. LICENSES_AUDIT.md §3.1 — требует юридической проверки для сценария
  on-premise поставки самих файлов словаря.**

## Модели (не PyPI-пакеты, только эксперимент, не в проде)

- `urchade/gliner_multi-v2.1` — Apache-2.0 — https://huggingface.co/urchade/gliner_multi-v2.1
- `urchade/gliner_multi_pii-v1` — Apache-2.0 — https://huggingface.co/urchade/gliner_multi_pii-v1
  (используются только в `experiments/a0_gliner/`, не входят в поставку продукта)

## Предобученные веса Natasha-стека (NewsEmbedding / NewsNERTagger / NewsMorphTagger / NewsSyntaxParser)

Распространяются проектами github.com/natasha/navec и github.com/natasha/slovnet
под тем же MIT LICENSE, что и код репозитория. См. LICENSES_AUDIT.md §3.2 —
рекомендовано письменное подтверждение статуса у мейнтейнеров перед крупной
коммерческой поставкой.

---

Полная таблица со всеми транзитивными зависимостями, версиями и
классификацией по риску — в [LICENSES_AUDIT.md](LICENSES_AUDIT.md).
