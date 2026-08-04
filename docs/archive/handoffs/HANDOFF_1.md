# HANDOFF — Блок 1: Экстрактор документов

> **Примечание аудита (2026-07-12).** Это исторический документ сдачи блока: он описывает
> состояние на момент сдачи и НЕ обновляется. Источник истины — `docs/archive/SHIFRATOR_SPEC_AI.md`
> (+ `docs/archive/specs/SHIFRATOR_SPEC_FILE_DECRYPT.md` для блоков 8–12) и `HANDOFF_CURRENT.md` в корне.
> После структурирования проекта все модули лежат в `src/` (импорты остались плоскими).
> **Что здесь устарело:** (1) вложенная ниже копия `entity_types.yaml` — версия ДО правок
> B1/B2 (паттерны реквизитов и SUM изменены, см. актуальный файл в корне); (2) не описан
> B5-fix: `_extract_txt` теперь проверяет результат декодирования эвристикой
> `_looks_like_mojibake` и кидает `ValueError("Не удалось надёжно определить кодировку…")`
> на UTF-16 без BOM — третий случай `ValueError` из `extract`.

## Что сделано
Созданы `models.py` (dataclass'ы `TextSegment`, `SourceDocument`, `Entity`), `entity_types.yaml` (посимвольная копия конфига из спецификации) и `extractor.py` с функцией `extract(path: str) -> SourceDocument`. `.docx` обходится через `document.element.body.iterchildren()` с определением типа по тегу (`w:p`/`w:tbl`), что сохраняет порядок параграфов и таблиц как в исходном файле. Merged-ячейки обрабатываются через множество `_tc`-элементов (`seen_tcs`), с сохранением ссылок на сами lxml-прокси. `.txt` читается с определением кодировки в порядке **BOM UTF-16 → `utf-8-sig` → `cp1251`**, переводы строк нормализуются перед разбиением на строки.

**Обновление (этап 2).** Битый/переименованный/пустой `.docx` больше не роняет пайплайн сырым `PackageNotFoundError`: `_extract_docx` перехватывает `PackageNotFoundError`/`zipfile.BadZipFile` от `Document(path)` и оборачивает в `ValueError`. `.txt` в UTF-16 (BOM `FF FE`/`FE FF`, «Юникод» из блокнота Windows) теперь распознаётся и декодируется как `utf-16` — до отката на `cp1251`, поэтому больше нет молчаливого моджибейка (ПДн сохраняются и детектируются).

## Публичный интерфейс

```python
def extract(path: str) -> SourceDocument
```
Импорт: `from extractor import extract`

Пример:
```python
from extractor import extract
doc = extract("contract.docx")
print(doc.segments[0].id, doc.segments[0].text, doc.segments[0].metadata)
# doc.source_format == "docx"; doc.source_path == "contract.docx"
```

Исключения:
- `FileNotFoundError` (встроенное) — если файла нет по указанному пути.
- `ValueError` (встроенное) — в двух случаях: (1) расширение (в нижнем регистре) не входит в `{".docx", ".txt"}`, сообщение `f"Неподдерживаемый формат: {ext}. Поддерживаются: .docx, .txt"`; (2) файл с расширением `.docx` не является корректным zip-контейнером (переименованный `.txt`, битый/обрезанный, пустой) — сообщение `f"Файл не является корректным .docx (повреждён или неверный формат): {path}"` (обёрнутый `PackageNotFoundError`/`BadZipFile` из python-docx).

Также доступны из `models.py`:
```python
from models import TextSegment, SourceDocument, Entity
```

## Формат данных на выходе

Пример `SourceDocument` (упрощённо, для .docx с параграфом, заголовком, таблицей 2x2, параграфом):
```python
SourceDocument(
    segments=[
        TextSegment(id="p0", text="Договор поставки", source_type="docx_paragraph",
                    metadata={"paragraph_index": 0, "style": "Heading 1"}),
        TextSegment(id="p1", text="Преамбула договора.", source_type="docx_paragraph",
                    metadata={"paragraph_index": 1, "style": "Normal"}),
        TextSegment(id="t0_r0_c0", text="Строка1 Кол1", source_type="docx_table_cell",
                    metadata={"table_index": 0, "row_index": 0, "col_index": 0}),
        TextSegment(id="t0_r0_c1", text="Строка1 Кол2", source_type="docx_table_cell",
                    metadata={"table_index": 0, "row_index": 0, "col_index": 1}),
        TextSegment(id="t0_r1_c0", text="Строка2 Кол1", source_type="docx_table_cell",
                    metadata={"table_index": 0, "row_index": 1, "col_index": 0}),
        TextSegment(id="t0_r1_c1", text="Строка2 Кол2", source_type="docx_table_cell",
                    metadata={"table_index": 0, "row_index": 1, "col_index": 1}),
        TextSegment(id="p2", text="Заключение договора.", source_type="docx_paragraph",
                    metadata={"paragraph_index": 2, "style": "Normal"}),
    ],
    source_format="docx",
    source_path="contract.docx",
)
```

Для .txt (файл "Первая строка\r\nВторая строка\r\n\r\nЧетвёртая\r\n", utf-8-sig):
```python
SourceDocument(
    segments=[
        TextSegment(id="l0", text="Первая строка", source_type="txt_line",
                    metadata={"line_index": 0, "encoding": "utf-8-sig"}),
        TextSegment(id="l1", text="Вторая строка", source_type="txt_line",
                    metadata={"line_index": 1, "encoding": "utf-8-sig"}),
        TextSegment(id="l2", text="", source_type="txt_line",
                    metadata={"line_index": 2, "encoding": "utf-8-sig"}),
        TextSegment(id="l3", text="Четвёртая", source_type="txt_line",
                    metadata={"line_index": 3, "encoding": "utf-8-sig"}),
    ],
    source_format="txt",
    source_path="sample.txt",
)
```

## Инварианты выходных данных

`TextSegment.metadata` по `source_type`:
- `"docx_paragraph"`: ключи `paragraph_index` (int, всегда присутствует) и `style` (str | None, всегда присутствует; `None`, если `paragraph.style is None`).
- `"docx_table_cell"`: ключи `table_index`, `row_index`, `col_index` (все int, все три всегда присутствуют).
- `"txt_line"`: ключи `line_index` (int, всегда присутствует) и `encoding` (str, одно из `"utf-16"`/`"utf-8-sig"`/`"cp1251"` — фактически применённая кодировка, всегда присутствует).

Инварианты, на которые следующие блоки могут полагаться без проверки:
- `SourceDocument.segments` строго в порядке следования элементов в теле документа (для .docx — как в `document.element.body`; для .txt — по номеру строки).
- `TextSegment.id` уникален в пределах `SourceDocument.segments`.
- Пустые сегменты (пустой параграф/строка/дублирующая merged-ячейка) присутствуют в списке, не пропущены.
- `source_type` всегда одно из трёх строковых значений, задаётся явно кодом (не проверяется отдельно на непустоту).
- Список не сортируется дополнительно — порядок и есть порядок появления, отдельного ключа сортировки нет.
- `Entity` в этом блоке не создаётся (используется только начиная с блока 2), но dataclass определён здесь и обязателен к импорту как есть.

## Что блок ожидает на входе (предусловия)

`extract` не принимает предварительно распарсенных структур — только путь к файлу. Проверяются существование файла (`Path(path).is_file()`) и расширение. Содержимое `.docx` заранее не парсится, но битый/не-zip `.docx` перехватывается при `Document(path)` и оборачивается в `ValueError` (см. «Исключения»), а не пробрасывается сырым.

## Использованные поля конфига

Блок 1 **не читает** `entity_types.yaml` в коде — он только создаёт этот файл как посимвольную копию из спецификации. Полное содержимое:

```yaml
entity_types:
  PERSON:
    method: ner
    ner_label: PER
    token_prefix: PERSON
  ORG:
    method: ner
    ner_label: ORG
    token_prefix: ORG
  ADDRESS:
    method: ner
    ner_extractor: addr        # НЕ ner_label/LOC — см. требования Блока 3: используется natasha.AddrExtractor, а не NER-тэггер
    token_prefix: ADDRESS
  INN:
    method: regex
    pattern: '\b\d{10}(\d{2})?\b'
    validate: inn_checksum
    token_prefix: INN
  OGRN:
    method: regex
    pattern: '\b\d{13}(\d{2})?\b'
    validate: ogrn_checksum
    token_prefix: OGRN
  KPP:
    method: regex
    pattern: '\b\d{9}\b'
    token_prefix: KPP
  BANK_ACCOUNT:
    method: regex
    pattern: '\b\d{20}\b'
    token_prefix: ACCOUNT
  BIK:
    method: regex
    pattern: '\b04\d{7}\b'
    token_prefix: BIK
  PASSPORT:
    method: regex
    pattern: '(?i)(?:паспорт|серия)[^\n\d]{0,20}(\d{2}\s?\d{2})\s?(\d{6})\b'
    token_prefix: PASSPORT
    # без контекстного якоря ("паспорт"/"серия") этот паттерн матчил бы любое 10-значное число —
    # у паспортных данных нет контрольной суммы, поэтому единственная защита от ложных срабатываний — якорь по контексту
  PHONE:
    method: regex
    pattern: '(?<!\d)(\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)'
    token_prefix: PHONE
  EMAIL:
    method: regex
    pattern: '[\w\.-]+@[\w\.-]+\.\w+'
    token_prefix: EMAIL
  SUM:
    method: regex
    pattern: '\d[\d  ]*[.,]?\d*\s?(руб(лей|ля|\.)?|₽|USD|\$|долл(аров|\.)?)(?![а-яА-ЯёЁ])'
    token_prefix: SUM
    # пробельный класс внутри числа — только пробел/неразрывный пробел ( ), не \s, чтобы не захватывать переводы строк
    # правая граница (?![а-яА-ЯёЁ]) — чтобы не съедать часть следующего слова, напр. "5 рубашек" не должно матчиться как "5 руб" + "ашек"
  DATE:
    method: regex
    pattern: '\d{1,2}[.\/]\d{1,2}[.\/]\d{2,4}'
    token_prefix: DATE
    enabled: false
```

(Проверено: неразрывный пробел ` ` внутри паттерна `SUM` сохранён в файле — не превратился в обычный пробел при записи.)

## Побочные эффекты импорта

`import extractor` — только импортирует `python-docx` (`from docx import Document`, ...) и `models`. Не загружает никаких моделей, не создаёт директорий, не читает файлов на диске. Быстро (доли секунды), лениво по своей природе — ничего не выполняется до вызова `extract()`.

## Зависимости и окружение

- `python-docx==1.2.0` (проверено — установлен в окружении).
- Python 3.11.9 (вывод `python --version`: `Python 3.11.9`).
- `PyYAML` не импортируется в `extractor.py` (сам файл `entity_types.yaml` не читается блоком 1), но пакет присутствует в `requirements.txt` для последующих блоков.

## Соглашения о путях и файлах времени выполнения

- `entity_types.yaml` лежит в корне репозитория, рядом с `models.py`/`extractor.py` — источник истины для блоков 2-4, блок 1 файлов на чтение не ожидает.
- `extract` не создаёт и не пишет никаких файлов — только читает по переданному пути.

## Отклонения от спецификации

Отклонений нет.

## Предложения по изменению интерфейсов

Нет предложений — интерфейс реализован как есть.

## Известные ограничения / TODO

- Область охвата — только `document.element.body`: колонтитулы, сноски, комментарии, текстовые поля/надписи (`w:txbxContent`) не извлекаются.
- Вложенные таблицы внутри ячеек не извлекаются (`cell.text` их игнорирует); при обнаружении такой ячейки в теле документа печатается предупреждение в stderr с координатами (`t{table_idx}_r{row_idx}_c{col_idx}`).
- `.txt` в UTF-16 распознаётся по BOM (`FF FE`/`FE FF`) и декодируется как `utf-16`; UTF-32 и UTF-16 **без** BOM не распознаются и пойдут по ветке `utf-8-sig`→`cp1251` (возможен `UnicodeDecodeError` или моджибейк) — вне зафиксированных требований блока.

## Данные для тестов

**Пример 1 — .docx с параграфом, заголовком, таблицей 2x2 и заключительным параграфом** (создан через `python-docx`: `add_heading("Договор поставки", level=1)`, `add_paragraph("Преамбула договора.")`, таблица 2x2 с текстом `"Строка{r}Кол{c}"`, `add_paragraph("Заключение договора.")`):
Вход: путь к этому .docx.
Выход: 7 сегментов в порядке `p0(Heading 1), p1(Normal), t0_r0_c0, t0_r0_c1, t0_r1_c0, t0_r1_c1, p2(Normal)` — таблица между `p1` и `p2`, не в конце списка.

**Пример 2 — merged-ячейка** (таблица 2x2, `cell(0,0).merge(cell(0,1))`, текст объединённой ячейки — "Объединённая ячейка"):
Выход: `t0_r0_c0` содержит `"Объединённая ячейка"`, `t0_r0_c1` содержит `""` (пустая строка, координата сохранена, а не пропущена).

**Пример 3 — .txt граничный случай** (файл, побайтово: `"Первая строка\r\nВторая строка\r\n\r\nЧетвёртая\r\n"`, кодировка utf-8-sig):
Выход: 4 сегмента — `l0="Первая строка"`, `l1="Вторая строка"`, `l2=""` (пустая строка между переводами сохранена), `l3="Четвёртая"`; завершающий `\r\n` не создаёт пятый пустой сегмент (`lines.pop()` отбрасывает финальный пустой элемент).

**Пример 4 — ошибки:**
- `extract("nope.docx")` → `FileNotFoundError`.
- `extract("file.pdf")` (существующий файл) → `ValueError("Неподдерживаемый формат: .pdf. Поддерживаются: .docx, .txt")`.
- `extract(<битый/переименованный/пустой .docx>)` → `ValueError("Файл не является корректным .docx (повреждён или неверный формат): <path>")` (обёрнутый `PackageNotFoundError`). ✅ проверено (`tests/test_adversarial_extractor.py::TestFakeOrCorruptDocx`).

**Пример 5 — .txt в UTF-16 (этап 2)** (файл, сохранённый как UTF-16 LE с BOM: `"ИНН 7707083893, телефон +7 495 123-45-67"`):
Выход: одна строка с читаемым текстом, `metadata["encoding"] == "utf-16"`, ИНН `7707083893` присутствует в тексте (детектируется дальше по пайплайну, а не превращается в моджибейк). ✅ проверено (`tests/test_adversarial_extractor.py::TestUtf16TxtSilentCorruption`).

## Файлы блока

- `models.py` (создан)
- `entity_types.yaml` (создан)
- `extractor.py` (создан)
