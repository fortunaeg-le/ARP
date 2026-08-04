# LEGAL_CHECK — фактическая проверка docs/PRODUCT_LEGAL.md

**Дата проверки:** 2026-07-28
**Проверяемый документ:** `docs/PRODUCT_LEGAL.md` (дата подготовки 2026-07-28)
**HEAD на момент проверки:** `b2a636e`
**Что здесь есть:** только технические факты со ссылкой на файл/строку или на
воспроизводимую команду. Юридических оценок, оценок риска и вердиктов
«готов / не готов» здесь нет — это вне области этой сессии.
**Что здесь НЕ сделано:** ни одна строка кода, конфига, корпуса или самого
`PRODUCT_LEGAL.md` не изменена. Исправления — отдельная сессия.

Пробы, которыми получены эмпирические ответы (пункты 3, 4, 9), лежали во
временном каталоге сессии и в репозиторий не добавлялись; их полный текст
воспроизводится из описаний ниже.

---

## 1. Цифры качества — **ОПРОВЕРГНУТО** (числа в документе устаревшие, не выдуманные)

В `PRODUCT_LEGAL.md` §5: recall 77.1 %, leak_v2 23.1 %, FP 1975, masking B 79.59 %.

### Фактические числа последнего сохранённого прогона

Источник — `tests/corpus/results_baseline.json` (полный дамп прогона 324
документов, из него `tests/corpus/gate.py` считает ВСЕ метрики, см.
[gate.py:10](tests/corpus/gate.py:10)).

Команда пересчёта (пересчёт дампа, корпус не гоняется):

```bash
venv/Scripts/python.exe -c "import sys,json;sys.path.insert(0,'tests/corpus');import measure_lib as ML;r=json.load(open('tests/corpus/results_baseline.json',encoding='utf-8'));a=ML.aggregate_results(r);t=a['total_bik_excl'];m=a['masking_correctness']['total'];print(100*t['found']/t['n'],100*t['leak_v2_6']/t['n'],a['fp_on_neg_total'],ML.mc_rates(m))"
```

| Метрика | `results_baseline.json` | `results_baseline_2b_6134547.json` |
|---|---|---|
| recall TOTAL (BIK-excl) | **81.09 %** | 77.07 % |
| leak_v2 ≥6 | **20.16 %** (2204 из 10932) | 23.08 % (2523) |
| leak_v2 ≥8 | **18.85 %** | 19.37 % |
| FP на объявленных негативах | **650** | 1975 |
| masking A (round-trip) | **100.00 %** | 100.00 % |
| masking B (границы) | **86.31 %** | 79.59 % |
| masking C (тип маски) | **95.07 %** | 90.81 % |

Все четыре числа документа — 77.1 / 23.1 / 1975 / 79.59 — **совпадают с
точностью до округления с `tests/corpus/results_baseline_2b_6134547.json`**,
дампом этапа 2b (коммит `6134547`, «feat(detect): регистровая детекция ФИО —
этап 2b», 2026-07-19, `git log -- tests/corpus/results_baseline_2b_6134547.json`).

### Откуда числа попали в документ

Файл, где лежат именно эти значения, существует: помимо самого дампа 2b это
`docs/archive/reports/HANDOFF_STAGE_2B.md:95-100` (recall 77.1 %, leak_v2 23.1 %,
FP 1975, MC B 79.59 %). Отчёт `docs/archive/reports/HANDOFF_S2.md:394-397, 447`
уже фиксирует переход на новые числа и прямо называет старые «было»
(«recall 77.1→80.5 %», «FP 1975→650», «B 79.59→86.79 %»).

### Дата снятия и состав набора

* `tests/corpus/results_baseline.json` — mtime **2026-07-25 19:49**, закоммичен
  `be5cb3e` (2026-07-25 21:29).
* `tests/corpus/results_gate_current.json` — mtime **2026-07-28 16:05**, sha256
  **побайтно совпадает** с `results_baseline.json`
  (`3cd63efd491044d53e07b97c4ef2b1a7b2fba27adb1f631ba4e19a26e9b5d1d4`). Это
  отладочный снимок текущего прогона, который `gate.py` пишет только при ПОЛНОМ
  прогоне корпуса ([gate.py:517](tests/corpus/gate.py:517)); совпадение байт
  означает, что прогон 2026-07-28 дал ровно тот же результат. Что файл получен
  прогоном, а не копированием, из самого файла установить нельзя.
* Состав набора (по `tests/corpus/gold.json`): **324 документа** — 162 `.txt` +
  162 `.docx`; 108 базовых + 216 мутированных (`combo`, `homoglyph`,
  `invisible`, `case`, `digit_spaces`, `linebreak`, `cell_split`).
  Эталонных сущностей 10 932 по 13 типам; категории: adversarial 6451,
  canonical 3013, ugly 1921.
* Числа `81.1 / 20.2 / 650 / 86.31 / 95.07` из `docs/archive/reports/HANDOFF_SUBSET_ITER.md:422-449`
  и `docs/archive/reports/CORPUS_V2_REPORT.md:234-236` воспроизводятся из этого дампа точно.

**Вывод по факту:** документ приводит метрики этапа 2b от 2026-07-19, тогда как
последний сохранённый прогон — от 2026-07-25/28 с другими значениями.

---

## 2. Авторство — **НЕ УСТАНОВЛЕНО** (данные истории приведены; выводов об авторстве не делаю)

```bash
git log --format='%an|%ae' | sort | uniq -c | sort -rn
```

Результат: **89 коммитов, один автор — `fortunaeg-le <fortunaeg@yandex.ru>`.**
Тот же и committer у всех 89 (`git log --format='%cn|%ce' | sort | uniq -c`).
Диапазон дат: 2026-07-12 … 2026-07-28.

Имя из git config (локальный и глобальный совпадают):

```bash
git config user.name; git config user.email
```
→ `fortunaeg-le` / `fortunaeg@yandex.ru`.

**Строка «Егор Лысакова» в git-истории и в коде отсутствует.** Единственное
вхождение по всему репозиторию (без `venv/`) — сама проверяемая строка
`docs/PRODUCT_LEGAL.md:232`. Откуда она взята — не установлено: ни git config,
ни `pyproject.toml`, ни `README*` этого имени не содержат.

Шаблоны сообщений коммитов:

```bash
git log --format='%B' | grep -i "claude" | sort | uniq -c | sort -rn
```

| Трейлер в сообщении коммита | Коммитов |
|---|---|
| `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` | 32 |
| `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` | 17 |
| `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` | 14 |
| `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` | 2 |
| `Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>` | 1 |
| **Итого коммитов с трейлером** | **66 из 89** |

Плюс один коммит, где Claude упомянут в заголовке, а не в трейлере
(«docs(audit): аудит эффективности сессий Claude Code…»).

Что этими данными **не** установлено: кто физически написал какую строку. Git
фиксирует, кто закоммитил и что записано в сообщении; авторство содержимого из
истории не выводится. Утверждения документа «основная логика детекции — человек»
/ «ИИ помогал при прототипировании» ни подтвердить, ни опровергнуть по git
нельзя. Замечание документа §6 «Репозиторий: Git (локальный, не публичный)» —
см. заметку в §11 ниже про `real_docs/`.

---

## 3. Содержимое `{sid}.doc.json` — **ОПРОВЕРГНУТО**

Документ (§1 и §7): «Содержит токены и ссылки на исходные значения, но сами
исходные значения хранятся в `.enc` файле».

### Что пишется по коду

Пишет [storage.py:292 `save_doc_segments`](src/storage.py:292). Записываются
**ровно два поля верхнего уровня и четыре поля на сегмент**:

| Поле | Что в нём |
|---|---|
| `source_format` | `"docx"` / `"txt"` |
| `segments[].id` | идентификатор сегмента (`p14`, `t0_r1_c2`) |
| `segments[].text` | **полный исходный текст сегмента открытым текстом** |
| `segments[].source_type` | `docx_paragraph` / `docx_table_cell` / … |
| `segments[].metadata` | структурные поля + то, что не отфильтровано (ниже) |

**Токенов в файле нет вообще** — ни поля `token`, ни ссылок на сущности; сам
докстринг модуля это и заявляет: «Содержит ПОЛНЫЙ исходный текст — ПДн»
([storage.py:37](src/storage.py:37), [storage.py:295](src/storage.py:295)).

### Поле с кэшем адресов

`_DOC_METADATA_DROP_KEYS` ([storage.py:283](src/storage.py:283)) вычищает перед
записью четыре служебных кеша: `_norm_cache`, `_anchor_search_cache`,
`_per_search_cache`, `detection_text`.

**`_addr_view_cache` в этот список не входит и записывается на диск.** Он
создаётся в [ner_detector.py:938-962](src/ner_detector.py:938) и содержит
`(norm_view, offset_map, glue_src)`, где `norm_view` — **нормализованная копия
текста сегмента открытым текстом**, а не ссылки.

### Эмпирика (проба на синтетическом документе корпуса)

Проба: подменён `session_store._DEFAULT_STORAGE_DIR` на временный каталог,
вызван `app/core.py::run_encrypt` на `tests/corpus/docs/agency_0002.docx`
(синтетика, ПДн нет).

```
файлы в хранилище:
   <sid>.doc.json     16659 байт
   <sid>.enc          15948 байт
   <sid>.meta.json       35 байт
   <sid>.txt           4981 байт
   <sid>.unread.json    616 байт
   key.bin               44 байт

.doc.json: ключи верхнего уровня ['source_format', 'segments'];  сегментов 43

ВСЕ поля сегмента (поле -> в скольки сегментах):
   id                          43
   source_type                 43
   text                        43
   metadata                    43
   metadata.paragraph_index    41
   metadata.style              41
   metadata.table_index         2
   metadata.row_index           2
   metadata.col_index           2
   metadata._addr_view_cache   32

служебные кеши текста в .doc.json:
   _norm_cache               в 0 сегментах
   _anchor_search_cache      в 0 сегментах
   _per_search_cache         в 0 сегментах
   detection_text            в 0 сегментах
   _addr_view_cache          в 32 сегментах  (из них 5 с НЕПУСТЫМ текстом)

суммарная длина открытого s.text: 3318 символов
```

Пример пары «текст сегмента / содержимое кеша» (значения обрезаны):

```
 id p14
   text  = '2.1. Общая цена Договора составляет 733 182,00 руб., в том числе НДС 20 %.'
   cache = '2.1. Общая цена Договора составляет 733182,00 руб., в том числе НДС 20 %.'
 id p30
   text  = 'Дополнительный телефон: +7\u200d (846) 4\u206055-90-65'
   cache = 'Дополнительный телефон: +7  (846) 4559065'
```

**Фактическое положение:** `{sid}.doc.json` — открытый JSON, содержащий полный
исходный текст документа (одна копия в `segments[].text`) плюс вторую,
нормализованную копию части сегментов в `metadata._addr_view_cache`. Токенов и
ссылок в нём нет.

---

## 4. Причина непрочитанных зон — **ОПРОВЕРГНУТО**

Документ §5: «Стек natasha не предоставляет API для их чтения».

### Чем читается .docx

[extractor.py:647 `_extract_docx`](src/extractor.py:647) — **python-docx**
(`from docx import Document`, [extractor.py:7](src/extractor.py:7)) плюс прямое
чтение XML через lxml для наследования капса. Обход —
`document.element.body.iterchildren()`, берутся только `w:p` и `w:tbl` верхнего
уровня тела.

Сканер зон [unread_zones.py](src/unread_zones.py) читает ZIP+lxml
(`from ooxml_core import parse_xml, read_zip_parts`,
[unread_zones.py:29](src/unread_zones.py:29)); в его докстринге прямо сказано,
что python-docx и есть источник слепоты ([unread_zones.py:14-17](src/unread_zones.py:14)).

**Natasha в чтении файла не участвует вообще.** `import natasha` есть только в
`src/ner_detector.py:65-68` — это детекция по УЖЕ извлечённому тексту.
Проверено: `grep -n "natasha" src/extractor.py src/unread_zones.py src/ooxml_core.py`
не даёт ни одного вхождения.

### Что читается, что нет (эмпирическая проба)

Собран .docx с маркерами в каждом виде узла, прогнан `extractor.extract` и
`unread_zones.scan_unread_zones`:

| Элемент .docx | extractor читает | объявлен непрочитанной зоной |
|---|---|---|
| обычный абзац `w:p` | **да** | — |
| таблица верхнего уровня | **да** (по коду, `w:tbl`) | — |
| результат поля `w:fldChar`/`w:t` (`FLDRESULT`) | **да** | — |
| колонтитул `word/header1.xml` | нет | **да** (`header`) |
| нижний колонтитул `word/footer1.xml` | нет | **да** (`footer`) |
| сноска / концевая сноска | нет | **да** (`footnote`/`endnote`, [unread_zones.py:240-244](src/unread_zones.py:240)) |
| примечание `w:comment` | нет | **да** (`comment`) |
| надпись `w:txbxContent` | нет | **да** (`textbox`) — проверено пробой |
| вложенная таблица `w:tbl` внутри `w:tc` | нет | **да** (`nested_table`) — проверено пробой |
| **отслеживаемая вставка `w:ins`** | **нет** | **нет** |
| **поле `w:fldSimple`** | **нет** | **нет** |
| **умный тег `w:smartTag`** | **нет** | **нет** |
| **элемент формы `w:sdt` (блочный)** | **нет** | **нет** |
| **элемент формы `w:sdt` (внутристрочный)** | **нет** | **нет** |
| удалённый текст `w:delText` | нет | нет (осознанно: невидимый текст, [unread_zones.py:93-96](src/unread_zones.py:93)) |
| код поля `w:instrText` | нет | нет (осознанно, там же) |

Вывод проб дословно:

```
  ОБЫЧНЫЙ          extractor=ЧИТАЕТ    зона=нет
  TRACKED_INS      extractor=НЕ читает зона=нет
  TRACKED_DEL      extractor=НЕ читает зона=нет
  FLDSIMPLE        extractor=НЕ читает зона=нет
  INSTR_МАРКЕР     extractor=НЕ читает зона=нет
  FLDRESULT        extractor=ЧИТАЕТ    зона=нет
  SMARTTAG         extractor=НЕ читает зона=нет
  SDT_BLOCK        extractor=НЕ читает зона=нет
  SDT_INLINE       extractor=НЕ читает зона=нет
  HEADER_МАРКЕР    extractor=НЕ читает зона=ДА
  FOOTER_МАРКЕР    extractor=НЕ читает зона=ДА
```

**Фактическое положение:** причина непрочитанных зон — уровень обхода тела
документа в `extractor._extract_docx` и модель python-docx, а не отсутствие API
у natasha. Пять видов узлов (`w:ins`, `w:fldSimple`, `w:smartTag`, `w:sdt`
блочный и внутристрочный) не читаются И не объявляются зоной. `w:delText` и
`w:instrText` не читаются по явно задокументированному решению (невидимое
содержимое).

---

## 5. Происхождение разметки — **ОПРОВЕРГНУТО**

Документ §4: «Разметка (gold): Ручная аннотация 13 типов сущностей…».

`gold.json` порождается программно, тем же генератором, что и документы:

* [tests/corpus/generate.py:921-937](tests/corpus/generate.py:921) — удаляет
  существующий `gold.json`, для каждой модели зовёт `gold_entry(m)` и пишет
  результат через `update_gold(root, entries)`.
* [corpus_lib.py:197 `gold_entry`](tests/corpus/corpus_lib.py:197) —
  `text, ents, negs, igns = serialize(model)`: и текст документа, и разметка
  получаются из ОДНОГО вызова над одной моделью.
* [corpus_lib.py:152 `serialize`](tests/corpus/corpus_lib.py:152) — собирает
  текст посимвольно и параллельно записывает точные смещения каждой сущности:
  `{"type": …, "start": rec["start"], "end": rec["end"], "text": text[start:end],
  "category": m["cat"]}`. Категория (`canonical`/`ugly`/`adversarial`) берётся из
  модели, а не проставляется человеком.
* [corpus_lib.py:520 `update_gold`](tests/corpus/corpus_lib.py:520) — сериализация
  в `gold.json`.

Ручного шага аннотации в этом пути нет. 13 типов — подтверждается подсчётом по
`gold.json` (см. §1: PER, ADDRESS, PHONE, INN, PASSPORT, ACCOUNT, EMAIL, ORG,
BIK, OGRN, KPP, BIRTHDATE, SNILS).

---

## 6. Форматы выдачи — **ЧАСТИЧНО ПОДТВЕРЖДЕНО** (все четыре записи в коде есть; но это не «выдача» одного цикла)

Документ §1: «Выходные при восстановлении: `.txt`, `.docx`, `.xlsx`, `.pptx`».

Где в коде запись каждого формата:

| Формат | Где пишется | Что именно |
|---|---|---|
| `.txt` | [shifrator.py:136](shifrator.py:136), [app/core.py:354](app/core.py:354) — `(storage_dir / f"{session_id}.txt").write_text(anon_text)` | **обезличенный** текст, пишется при шифрации |
| `.docx` | [file_detokenizer.py:33-34](src/file_detokenizer.py:33) → `docx_rewriter.rewrite` | восстановление |
| `.xlsx` | [file_detokenizer.py:35-36](src/file_detokenizer.py:35) → `xlsx_rewriter.rewrite` | восстановление |
| `.pptx` | [file_detokenizer.py:37-38](src/file_detokenizer.py:37) → `pptx_rewriter.rewrite` | восстановление |

Уточнения, установленные по коду:

1. **ВХОДНЫЕ форматы — только `.docx` и `.txt`**
   ([extractor.py:818-823](src/extractor.py:818): всё прочее → `ValueError`;
   в UI — `_ALLOWED_EXT = (".docx", ".txt")`, [app/server.py:32](app/server.py:32)).
   Обезличить `.xlsx`/`.pptx` система не может.
2. **`detokenize_file` не восстанавливает `.txt`**: `_SUPPORTED = (".docx",
   ".xlsx", ".pptx")` ([file_detokenizer.py:25](src/file_detokenizer.py:25)),
   `.txt` даёт `ValueError` ([file_detokenizer.py:80](src/file_detokenizer.py:80)).
   Восстановление текста идёт через `detokenizer.detokenize` и печатается в
   stdout (`shifrator.py cmd_decrypt`) либо возвращается в UI
   ([app/core.py:379 `run_decrypt`](app/core.py:379)) — файл при этом не пишется.
3. **Файловое восстановление доступно только из CLI** (`decrypt-file`,
   [shifrator.py:294](shifrator.py:294)); в `app/server.py` эндпоинта на
   `detokenize_file` нет (`grep -n "detokenize_file" app/` — пусто).
4. `.docx`/`.xlsx`/`.pptx` на восстановлении — это перезапись **файла, который
   пользователь принёс сам** с токенами внутри; система такого файла на этапе
   шифрации не порождает.

**Формат, который не поддерживается так, как описано:** `.txt` как выход
восстановления файлом — его нет; и `.xlsx`/`.pptx` не могут быть входом цикла.

---

## 7. Удаление сессий — **ОПРОВЕРГНУТО** (оба утверждения документа неточны)

Документ §1: «Сессии автоматически удаляются через 24 часа».
Документ §7: «Удаление проверяется только при доступе к папке (`purge_expired`)».

### Точный механизм по коду

Никакого таймера, планировщика или фонового потока в коде нет. Есть ровно два
места, где TTL вообще влияет на файлы:

1. **`purge_expired`** — вызывается **из единственной точки продукта**:
   [shifrator.py:181](shifrator.py:181), в начале CLI-команды `decrypt`.
   `grep -rn "purge_expired" app/ src/ shifrator.py` даёт в `app/` **ноль**
   вызовов. То есть **десктопный интерфейс не удаляет просроченные сессии
   никогда**.
   Что делает: перебирает `*.enc`, расшифровывает, сравнивает `expires_at`,
   удаляет `.enc` ([session_store.py:359-361](src/session_store.py:359)) и
   сайдкар `.txt` ([session_store.py:372-375](src/session_store.py:372)); затем
   `storage.purge_expired` подчищает осиротевшие `.meta.json` / `.doc.json`
   ([storage.py:234-243](src/storage.py:234)).
2. **`load_session`** — при обращении к истёкшей сессии кидает
   `SessionExpiredError` ([session_store.py:293-295](src/session_store.py:293)).
   **Файл при этом не удаляется** — данные остаются на диске.

### Что происходит, если программу не открывали неделю

Ничего. Файлы `.enc`, `.txt`, `.doc.json`, `.meta.json`, `.unread.json`,
`key.bin` остаются на диске в неизменном виде: удаление запускается только
процессом продукта, а он не запускался. Первый после этого запуск
**CLI `decrypt`** удалит просроченные `.enc`/`.txt` и осиротевшие
`.meta.json`/`.doc.json`. Если пользователь работает только через интерфейс
(`app/`), не удалится ничего и никогда.

### Дополнительный факт: `.unread.json` не удаляется ни одним путём

`grep -rn "unread" src/*.py` даёт только упоминания в комментариях
и в `unread_zones.zones_to_json`. Ни `session_store.delete_session`
([session_store.py:427-459](src/session_store.py:427) — удаляет `.enc` и `.txt`),
ни `storage.delete_session` ([storage.py:200-219](src/storage.py:200) — плюс
`.meta.json`/`.doc.json`), ни `purge_expired` файл `{sid}.unread.json` не
трогают. Это тот самый файл, который документ §7 помечает как содержащий
открытые ПДн.

---

## 8. Цифра ORG 94 % — **ОПРОВЕРГНУТО** (число из более раннего этапа)

Документ §3: «ORG обнаруживается на 94 %, PER на 72 %».

Фактические значения по `tests/corpus/results_baseline.json` (та же команда, что
в §1, разрез по типам):

| Показатель ORG | Значение |
|---|---|
| **recall ORG** | **97.19 %** (n = 462) |
| **masking B по ORG** (границы масок) | **92.23 %** |
| **precision ORG** | **99.78 %** (tp 457, fp_neg 1) |

Для PER в том же дампе: **recall 74.06 %** (n = 3420), masking B 87.90 %,
precision 99.96 %.

Откуда 94 %: `docs/STATE.md:279, 300, 355` фиксирует «ORG recall 93.9→94.2 %» и
«94.16 %→94.16 %» на этапах A′/C′, `docs/archive/reports/HANDOFF_S2.md:415` —
«ORG 94.41 %». Это более ранние прогоны; в последнем сохранённом дампе значение
97.19 %. Названные в постановке 92.2 % — это **masking B по ORG** (92.23 %),
другая метрика, не recall. 72 % близко к ORG-независимому PER этапа 2b
(71.96 % в `results_baseline_2b_6134547.json`), а не к текущим 74.06 %.

---

## 9. Колонтитулы при восстановлении — **ПОДТВЕРЖДЕНО с уточнением**

Документ §1: выходной `.docx` сохраняет исходное форматирование.

### По коду

[docx_rewriter.py:41-46](src/docx_rewriter.py:41) — `_PART_MASKS` включает
`word/document.xml`, `word/header*.xml`, `word/footer*.xml`, `word/footnotes.xml`,
`word/endnotes.xml`. Модуль не использует python-docx намеренно
([docx_rewriter.py:12-13](src/docx_rewriter.py:12): «python-docx запрещён —
пересобирает документ по своей модели и теряет неизвестную ему разметку»);
ZIP пересобирается из исходных байтов, меняются только затронутые части.

### Эмпирика

Проба: .docx с токеном `[PER_1]` в теле и `[ORG_1]` в колонтитуле, вызван
`file_detokenizer.detokenize_file`.

```
замен: 2  нераскрытых: []
тело      : Договор с Иванов Иван Иванович в теле.
колонтитул: ШАПКА: ООО «Ромашка», токен в колонтитуле
подвал    : ПОДВАЛ: без токена
части ZIP: было 19, стало 19; исчезли []; появились []
частей побайтно неизменных: 16 из 19
ИЗМЕНЁННЫЕ части: ['word/document.xml', 'word/footer1.xml', 'word/header1.xml']
```

**Фактическое положение:** колонтитулы **остаются как были** в файле, который
принёс пользователь, и токены внутри них раскрываются. Части `header1.xml` /
`footer1.xml` перезаписываются (пере-сериализация XML) даже когда токенов в них
нет — при неизменном видимом тексте; остальные 16 частей архива побайтно
неизменны.

Уточнение, важное для чтения §1 документа: копирования колонтитулов «из
исходника» не происходит и происходить не может — путь восстановления файла
(`detokenize_file`) исходный документ не открывает и о нём ничего не знает; он
работает только с принесённым файлом и сессией. Исходный документ на этапе
шифрации в `.docx` не сохраняется — сохраняется только обезличенный `{sid}.txt`.

---

## 10. Сеть — **ОПРОВЕРГНУТО** (сетевых загрузок нет вообще, в том числе при первом запуске)

Документ §1: «Сетевые запросы существуют только для загрузки предобученных
моделей NLP при первом запуске (natasha, pymorphy2 скачивают веса с GitHub)».

### Обращения к сети в коде продукта

`grep -rn "requests\|urllib\|socket\|urlopen\|https://" src/*.py shifrator.py app/*.py`:

* `src/` и `shifrator.py` — **ни одного** сетевого обращения. Совпадения в
  `src/*.py` — это строки XML-namespace (`http://schemas.openxmlformats.org/…`),
  не URL для загрузки.
* `app/server.py` — `http.server`, привязка к `HOST = "127.0.0.1"`
  ([app/server.py:28](app/server.py:28), [app/server.py:570](app/server.py:570)).
* `app/launcher.py:41-44` — `urllib.request.urlopen("http://127.0.0.1:{port}/api/ping")`,
  проверка живости собственного сервера (петля).
* `app/launcher.py:72` — `webbrowser.open("http://127.0.0.1:{port}/")`.

### Чем скачиваются модели natasha

Ничем — **веса поставляются внутри пакета** `natasha==1.6.0`:

```bash
venv/Scripts/python.exe -c "import natasha,os;print(os.path.dirname(natasha.__file__))"
```

Содержимое `venv/Lib/site-packages/natasha/data/`:

| Файл | Размер |
|---|---|
| `emb/navec_news_v1_1B_250K_300d_100q.tar` | 26 634 240 |
| `model/slovnet_ner_news_v1.tar` | 2 385 920 |
| `model/slovnet_morph_news_v1.tar` | 2 580 480 |
| `model/slovnet_syntax_news_v1.tar` | 2 611 200 |

Загрузчики берут локальный путь по умолчанию: `natasha/emb.py` —
`class NewsEmbedding(Embedding): def __init__(self, path=NEWS_EMBEDDING)`;
`natasha/ner.py` — `class NewsNERTagger(NERTagger): def __init__(self, emb, path=NEWS_NER)`,
где `NEWS_EMBEDDING`/`NEWS_NER` — константы путей из `natasha/data`. Сетевого
кода на пути инференса нет (`navec/train/s3.py` относится к обучению и при
инференсе не импортируется).

То же для сборки дистрибутива: `packaging/shifrator.spec:92` —
`datas += collect_data_files("natasha")` («веса navec/slovnet под Natasha»),
`packaging/shifrator.spec:42` перечисляет `natasha, navec, slovnet, razdel,
yargy, pymorphy2` как hidden imports. `pymorphy2` берёт словарь из пакета
`pymorphy2-dicts-ru` (обычная зависимость из `requirements.txt`), не из сети.

**Можно ли поставить полностью без сети:** установка пакетов требует источника
дистрибутивов (сеть либо локальное зеркало/wheelhouse) — это установка Python-
пакетов, а не «загрузка моделей при первом запуске». После установки первый и
все последующие запуски сетевых обращений наружу не делают.

---

## 11. Шифрование — **ПОДТВЕРЖДЕНО частично; про «ключи уничтожаются» — ОПРОВЕРГНУТО**

### Один ключ на все сессии — ПОДТВЕРЖДЕНО

[session_store.py:36](src/session_store.py:36): `_KEY_FILENAME = "key.bin"`.
[session_store.py:68 `_load_or_create_key`](src/session_store.py:68): если
`key.bin` есть — читается (`key_path.read_bytes()`), иначе создаётся
`Fernet.generate_key()` и публикуется атомарно через `os.link`
([session_store.py:91-124](src/session_store.py:91)). Один ключ на директорию
хранилища; ротации в коде нет.

**Перечитывается ли:** да, с диска при каждой операции — `save_session`,
`load_session`, `purge_expired` ([session_store.py:326](src/session_store.py:326)),
`list_sessions` ([session_store.py:403](src/session_store.py:403)) читают файл
заново; кеша ключа в памяти модуля нет.

**Права:** `_chmod_600` ([session_store.py:60-66](src/session_store.py:60)) —
`if os.name == "posix"`. **На Windows права не выставляются** (функция тихо
ничего не делает).

**Путь:** `_DEFAULT_STORAGE_DIR = Path.home() / ".shifrator" / "sessions"`
([session_store.py:33](src/session_store.py:33)). На Windows это
`C:\Users\<имя>\.shifrator\sessions`, **не** `AppData\Roaming\.shifrator\`, как
указано в документе §1.

### Что происходит при удалении сессии

`storage.delete_session` ([storage.py:200](src/storage.py:200)) →
`session_store.delete_session` ([session_store.py:427](src/session_store.py:427)):

* `enc_path.unlink()` — **удаление файла**, содержимое не затирается;
* `txt_path.unlink()` — то же;
* затем `storage` удаляет `.meta.json` и `.doc.json`
  ([storage.py:212-216](src/storage.py:212));
* `.unread.json` не удаляется (см. §7);
* **`key.bin` никогда не трогается** — это записано в самом докстринге
  ([session_store.py:432](src/session_store.py:432): «key.bin никогда не трогает»)
  и в `purge_expired` ([session_store.py:303](src/session_store.py:303)).

Перезаписи содержимого (wipe) нигде нет: `grep -n "os.urandom\|shred\|overwrite"
src/session_store.py src/storage.py` — пусто.

**Фактическое положение:** утверждение документа §1 «После удаления восстановить
исходные данные невозможно (ключи уничтожаются)» неверно в части механизма: ключ
общий, при удалении сессии он остаётся; удаляется только зашифрованный файл, и
удаляется обычным `unlink` без затирания.

### Заметка по §6 документа («репозиторий не публичный»)

Не проверялось в этой сессии и вне её области. Фиксирую лишь то, что в памяти
проекта зафиксировано обратное относительно каталога `real_docs/`; установить
статус репозитория средствами кода нельзя — **не установлено**.

---

## 12. Хранение ручной разметки — **ПОДТВЕРЖДЕНО в части числа 30; механизм очистки существует, но НЕ ВЫЗЫВАЕТСЯ**

Документ §1: «Сохраняются отдельно от основной сессии и хранятся 30 дней».

Число 30 в коде есть: [storage.py:129](src/storage.py:129)
`_MARKUP_TTL_DAYS = 30`, с обоснованием в комментарии
[storage.py:122-128](src/storage.py:122).

Механизм очистки есть: [storage.py:433 `purge_expired_markup`](src/storage.py:433)
— удаляет ЗАПИСИ старше `ttl_days` по `created_at` каждой записи, файл сессии с
полностью истёкшими записями удаляет целиком; записи без валидного `created_at`
не трогает.

**Кто его вызывает:**

```bash
grep -rn "purge_expired_markup" --glob '!venv/**' .
```

| Файл | Строка | Что это |
|---|---|---|
| `src/storage.py` | 59, 113, 231, 433 | докстринг, `__all__`, ссылка в комментарии, определение |
| `tests/test_storage_s1.py` | 143 | тест |
| `docs/archive/reports/HANDOFF_S1.md` | 50, 272, 300 | отчёт |

**В `app/` и `shifrator.py` вызовов нет.** `app/server.py` при старте вызывает
только `migrate_legacy_markup()` ([app/server.py:556-565](app/server.py:556)).
Тот же отчёт `docs/archive/reports/HANDOFF_S1.md:300` прямо относит
«автоматический периодический вызов `purge_expired_markup()`» к несделанному.

Что удаляет разметку фактически — только явные действия пользователя:
`delete_all_markup()` ([app/server.py:426-427](app/server.py:426)) и
`delete_session(session_id, delete_markup=True)`
([app/server.py:418](app/server.py:418)).

**Фактическое положение:** порог 30 дней объявлен в коде и реализован функцией,
но ни один путь продукта её не вызывает — по истечении 30 дней разметка сама не
удаляется. Хранится она в `~/.shifrator/markup/` — отдельно от сессий
([storage.py:133-136](src/storage.py:133)), это часть утверждения документа
подтверждается; поле `value` записи — фрагмент реального текста открытым текстом
([storage.py:361-367](src/storage.py:361)).

---

## Сводка

| № | Утверждение | Вердикт |
|---|---|---|
| 1 | Цифры качества 77.1 / 23.1 / 1975 / 79.59 | **ОПРОВЕРГНУТО** — это дамп этапа 2b (2026-07-19); последний прогон: 81.09 / 20.16 / 650 / 86.31 / 95.07 |
| 2 | Авторство «Егор Лысакова», ИИ при прототипировании | **НЕ УСТАНОВЛЕНО** — 89 коммитов одного git-автора `fortunaeg-le`; имени из документа в истории и коде нет; 66 коммитов несут `Co-Authored-By: Claude` |
| 3 | `.doc.json` — «токены и ссылки» | **ОПРОВЕРГНУТО** — токенов нет; полный исходный текст открытым текстом + вторая копия в `_addr_view_cache` |
| 4 | Причина непрочитанных зон — «нет API у natasha» | **ОПРОВЕРГНУТО** — natasha в чтении не участвует; плюс 5 видов узлов не читаются И не объявляются зоной |
| 5 | «Ручная аннотация 13 типов» | **ОПРОВЕРГНУТО** — gold.json порождается `generate.py`/`serialize()` вместе с документом |
| 6 | Выдача в .txt/.docx/.xlsx/.pptx | **ЧАСТИЧНО** — все четыре записи есть, но `.txt` не восстанавливается файлом, `.xlsx`/`.pptx` не могут быть входом, файловое восстановление только в CLI |
| 7 | Автоудаление через 24 ч / «проверяется при доступе к папке» | **ОПРОВЕРГНУТО** — единственный вызов `purge_expired` в CLI `decrypt`; интерфейс не удаляет никогда; `.unread.json` не удаляется ничем |
| 8 | ORG 94 % | **ОПРОВЕРГНУТО** — в последнем прогоне recall ORG 97.19 %; 92.2 % — это masking B по ORG |
| 9 | Колонтитулы при восстановлении | **ПОДТВЕРЖДЕНО с уточнением** — сохраняются из принесённого файла, токены в них раскрываются; из исходника ничего не копируется |
| 10 | Сеть только для загрузки моделей при первом запуске | **ОПРОВЕРГНУТО** — веса внутри пакета natasha; сетевых обращений наружу нет ни при первом, ни при последующих запусках |
| 11 | Один ключ, `key.bin` | **ПОДТВЕРЖДЕНО**; «ключи уничтожаются при удалении» — **ОПРОВЕРГНУТО** (ключ не трогается, файл сессии удаляется `unlink` без затирания) |
| 12 | Разметка хранится 30 дней | **ПОДТВЕРЖДЕНО частично** — `_MARKUP_TTL_DAYS = 30` и `purge_expired_markup()` есть, но ни один путь продукта их не вызывает |
