# CODEMAP — карта кода SHIFRATOR

Построена ПО КОДУ (`src/`, `shifrator.py`), не по докам. Цель — дать читателю без открытого
кода понять, какой модуль за что отвечает и как течёт `encrypt`/`decrypt`. Имена функций —
реальные; номера строк не приводятся (гниют).

Правило импорта: модули `src/` импортируются **плоско** (`from tokenizer import tokenize`).
`src/` на `sys.path` кладут: editable-установка в venv, корневой `conftest.py` (pytest),
bootstrap в `shifrator.py` (CLI).

---

## Путь данных

### encrypt (`shifrator.py:cmd_encrypt`)
```
extract(path)                      # .docx/.txt -> SourceDocument (сегменты + detection_text)
  -> scan_unread_zones(path)                   # этап 1b: зоны, которые extract НЕ читает
     # strict (умолчание): зоны найдены -> таблица на stdout + exit 2, сессии НЕ будет
     # --allow-lossy / strict_zones: false: предупреждение + {sid}.unread.json (сырой текст)
     # .txt читается целиком — политика к нему неприменима
  -> detect_regex(doc, cfg)                    # реквизиты; считаются ПЕРВЫМИ
  -> detect_ner(doc, cfg, regex_entities=…)    # PER/ORG/LOC + адрес; regex как барьеры
  -> merge_compound_entities(doc, entities)    # синтаксис: «ИП + ФИО» -> один ORG
  -> tokenize(doc, entities, cfg)              # разрешение пересечений + B3 + токены
  -> save_session(final_entities)              # Fernet -> {sid}.enc; вернуть session_id
анон-текст пишется в {storage}/{sid}.txt; в stdout — ТОЛЬКО session_id.
```

### decrypt (`shifrator.py:cmd_decrypt`)
```
stdin -> detokenize(text, session_id) -> stdout   # замена [TYPE_N] обратно; о неразрешённых — в stderr
```

### decrypt-file (`shifrator.py:cmd_decrypt_file`) — компонент 2
```
detokenize_file(session_id, path, out)
  -> _load_adapter(ext)                # .docx/.xlsx/.pptx -> нужный *_rewriter
  -> <adapter>.rewrite(src, dst, resolve)
       -> ooxml_core: read_zip_parts / parse_xml / replace_tokens_in_group / rewrite_zip
файл записывается как {имя}_restored.{ext}; оформление сохраняется.
```

### delete (`shifrator.py:cmd_delete`)
```
delete_session(session_id) -> удаляет {sid}.enc (и сопутствующий {sid}.txt)
```

---

## Модули `src/`

| Модуль | Ответственность | Ключевые публичные функции | Кто вызывает |
|---|---|---|---|
| `models.py` | Датаклассы формата | `TextSegment`, `SourceDocument`, `Entity` | все |
| `extractor.py` | `.docx/.txt` → `SourceDocument`; нормализация регистра (`detection_text`), наследование caps от стилей; неверный формат → `ValueError` | `extract(path)` | `cmd_encrypt` |
| `unread_zones.py` | **Этап 1a.** Обнаружение зон `.docx`, которые `extractor` НЕ читает (колонтитулы, сноски, надписи, вложенные таблицы). Через zip+lxml, **не** python-docx (он и есть источник слепоты). Зоны не читает — только находит | `scan_unread_zones(path) -> list[Zone]`, `zones_table`, `zones_to_json`; искл. `UnreadZoneError`; тип `Zone` | `cmd_encrypt`, `tests/corpus/run_measurement.py` |
| `regex_detector.py` | Реквизиты (ИНН/ОГРН/тел/почта/сумма) с чек-суммами | `detect_regex(doc, cfg)`, `inn_checksum`, `ogrn_checksum` | `cmd_encrypt`, `tokenize` (B3-окно) |
| `ner_detector.py` | Natasha PER/ORG/LOC + **гибридная детекция адреса** (LOC/маркеры + yargy в окне) + **барьеры** расширения | `detect_ner(doc, cfg, regex_entities=…)`; внутр.: `_address_barriers`, `_expand_addr_right/left`, `_filter_suspect_yargy`, `_build_address_spans` | `cmd_encrypt`, `tokenize` (B3-окно) |
| `syntax_compound.py` | Склейка «ORG + ФИО» (ИП Пирогова А.С.) в один ORG по appos-ребру Natasha | `merge_compound_entities(doc, entities)`; внутр.: `_appos_links` (направление ребра!) | `cmd_encrypt` |
| `tokenizer.py` | Разрешение пересечений; B3-проход по граничным окнам соседних сегментов; ФИЛЬТР ТИПОВ (T1); присвоение токенов; сборка анон-текста | `tokenize(doc, entities, cfg, enabled_types=None)` = `resolve_for_masking` (дорогая часть, от набора НЕ зависит) + `apply_masking` (единственная точка фильтра T1), `build_plain_text(doc)`; внутр.: `_resolve_overlaps`, `_trim_to_free`, `_winner`, `_detect_boundary_entities`, `_boundary_sep` | `cmd_encrypt` |
| `type_policy.py` | ЭТАП T1: какие типы маскировать — четыре набора, перекрытия по типу, `~/.shifrator/settings.json` (неизвестный тип/набор игнорируются) | `known_types(cfg)`, `resolve(profile, overrides, known)`, `load_settings()`, `enabled_types(cfg)`, `describe(cfg, enabled)`; `MAXIMUM is None` | `cmd_encrypt`, `app/core.current_policy` |
| `session_store.py` | Fernet-хранилище токен-мапы; TTL, автоочистка | `save_session`, `load_session`, `list_sessions`, `delete_session`, `purge_expired`, `default_storage_dir`; искл. `SessionNotFoundError`, `SessionExpiredError` | `storage.py`, `detokenize*` (напрямую) |
| `storage.py` | **Этап U1.** Тонкий интерфейс хранения (задел серверной версии) поверх `session_store.py` — CLI/UI ходят сюда, не в `session_store` напрямую | реэкспорт `session_store`'а + `save_markup` (заглушка `NotImplementedError`) | `shifrator.py`, `app/core.py`, `app/server.py` |
| `detokenizer.py` | Обратная замена `[TYPE_N]` → значение в ТЕКСТЕ | `detokenize(text, sid) -> (text, unresolved)` | `cmd_decrypt` |
| `file_detokenizer.py` | Обратная замена в ФАЙЛЕ; выбор адаптера по расширению | `detokenize_file(sid, path, out)`; искл. на неподдерж. формат | `cmd_decrypt_file` |
| `ooxml_core.py` | Общий слой OOXML: ZIP+XML чтение/запись, замена токенов в группе run'ов (в т.ч. разорванных между run'ами), защита от zip-бомб | `read_zip_parts`, `rewrite_zip`, `parse_xml`, `serialize_xml`, `replace_tokens_in_group`; искл. `OoxmlError`; типы `TextUnit`, `RunGroup` | `*_rewriter` |
| `docx_rewriter.py` / `xlsx_rewriter.py` / `pptx_rewriter.py` | Формат-специфичные адаптеры поверх `ooxml_core` | `rewrite(src, dst, resolve) -> (count, unresolved)` | `file_detokenizer` |

## Данные и конфиг

- **`Entity`** (`models.py`): `segment_id`, `start`, `end`, `original_text` (== `segment.text[start:end]`),
  `entity_type`, `detector` (`"regex"`/`"ner"`), `confidence`, `token` (проставляется в `tokenize`).
- **`entity_types.yaml`** (рядом с `shifrator.py`) — типы сущностей, их regex/приоритеты и
  префиксы токенов. Путь передаётся как `--config`. Ключ верхнего уровня **`strict_zones`**
  (этап 1b, умолчание `true`) читает только блок 7 (`shifrator.py:_read_strict_zones`);
  детекторы его игнорируют. Нечитаемый конфиг ⇒ `true` (fail-closed).
- **Sidecar `{session_id}.unread.json`** (рядом с `{sid}.txt` и `{sid}.enc`) — появляется
  только в lossy-режиме; содержит СЫРОЙ текст непрочитанных зон (ПДн!) и служит отметкой
  «сессия lossy». Формат `{sid}.enc` (контракт блока 5) ради этой отметки НЕ расширялся.
- **Токен**: `[<PREFIX>_<N>]`, нумерация сквозная по типу; переиспользование по
  `(entity_type, original_text)` (`tokenizer.py`, блок присвоения токенов).

## Тесты и харнесс

- `tests/` — pytest; `tests/component2/` — компонент 2; `tests/golden_addresses.py` +
  `tests/test_golden_addresses.py` — golden-набор адресов (89 адр. + 16 не-адресов).
- `bench/` — воспроизводимый регрессионный/замерный харнесс (`bench/README.md`); запуск из
  корня `venv/Scripts/python.exe bench/<script>.py`.

## Глубокие спеки

- Компонент 2 (OOXML-детокенизация, blocks 8–12), подробный алгоритм и краевые случаи —
  `docs/SHIFRATOR_SPEC_FILE_DECRYPT.md`.
- Инварианты детекции/токенизации (B3-разделитель, разрешение пересечений, раздельные токены
  B3-пар) — [ARCHITECTURE.md](ARCHITECTURE.md).
