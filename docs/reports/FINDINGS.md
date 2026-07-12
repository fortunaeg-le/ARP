# FINDINGS — этап 2 (целенаправленная охота за критическими ошибками использования)

Область: реальные "грязные" сценарии эксплуатации SHIFRATOR, не тепличные граничные
случаи (те покрыты этапом 1, 138 зелёных тестов). Код НЕ чинился — только находки и
их документация. Все находки воспроизводятся тестами в `tests/test_adversarial_*.py`.

**Как читать статус:**
- `XFAIL (bug)` — тест проверяет КОРРЕКТНОЕ поведение, которое сейчас нарушено; помечен
  `@pytest.mark.xfail(strict=True)`, поэтому воспроизводит дефект и «покраснеет» (XPASS→FAIL),
  как только код починят и метку забудут снять.
- `PASS (pins)` — тест фиксирует фактическое (нежелательное или пограничное) поведение
  как есть, чтобы отследить его изменение.
- `PASS (спека OK)` — подтверждение, что обещание спеки/защита реально работает (не дефект).

## Таблица находок (по убыванию критичности)

| № | Блок | Сценарий | Критичность | Статус | Тест | Спека / пробел | Как исправлено |
|---|------|----------|-------------|--------|------|----------------|----------------|
| 1 | 5 session_store | Два одновременных `encrypt` на свежую директорию: гонка в `_load_or_create_key` (TOCTOU) перезаписывает `key.bin`, одна сессия становится нерасшифровываемой навсегда | **КРИТИЧНО** (тихая потеря токен-маппинга) | FIXED | `test_adversarial_session_store.py::TestConcurrentKeyCreationRace::test_two_concurrent_saves_on_fresh_dir_both_recoverable` | Пробел в спеке (конкурентность не описана) | Атомарное создание ключа: запись во временный файл + публикация под финальным именем через `os.link` (эксклюзивно); проигравший гонку читает уже целиком записанный ключ победителя |
| 2 | 5 session_store | Повреждённый `key.bin` → `load_session` кидает неперехваченный `ValueError` из `Fernet()` вместо `SessionNotFoundError` | **КРИТИЧНО** (крах вместо чистой ошибки) | FIXED | `...TestCorruptedKeyFile::test_load_session_corrupted_key_raises_session_not_found` | Противоречит спеке блока 5: «при отсутствии файла или … кидает явное исключение», ловит только `InvalidToken` | `Fernet(key)` в `load_session` обёрнут в `try/except ValueError` → `SessionNotFoundError` |
| 3 | 5 session_store | Повреждённый `key.bin` → `purge_expired` падает `ValueError`; CLI `decrypt` зовёт его ПЕРВЫМ ⇒ вся команда рушится с трейсбеком | **КРИТИЧНО** (крах на нормальном вводе) | FIXED | `...TestCorruptedKeyFile::test_purge_expired_corrupted_key_does_not_crash` | Пробел в спеке (битый ключ в purge не описан) | `Fernet(...)` в `purge_expired` обёрнут в `try/except ValueError` → предупреждение в stderr + `return 0` |
| 4 | 1 extractor | `.txt`, переименованный в `.docx` (или битый/обрезанный `.docx`), → `PackageNotFoundError` из python-docx, не обёрнут; CLI ловит только `FileNotFoundError`/`ValueError` | **КРИТИЧНО** (крах на реальном вводе) | FIXED | `test_adversarial_extractor.py::TestFakeOrCorruptDocx::test_text_file_renamed_to_docx_raises_value_error` | Пробел в спеке (битый .docx не описан явно) | `Document(path)` в `_extract_docx` обёрнут в `try/except (PackageNotFoundError, BadZipFile)` → `ValueError` с понятным сообщением |
| 5 | 1 extractor | Обрезанный/битый `.docx` (валидный zip-заголовок, но неполный) → та же `PackageNotFoundError` | **КРИТИЧНО** | FIXED | `...TestFakeOrCorruptDocx::test_truncated_docx_raises_value_error` | Пробел в спеке | Тот же `try/except` вокруг `Document(path)` (см. #4) |
| 6 | 1 extractor | Пустой `.docx` (0 байт) → та же `PackageNotFoundError` | **КРИТИЧНО** | FIXED | `...TestFakeOrCorruptDocx::test_empty_docx_raises_value_error` | Пробел в спеке | Тот же `try/except` вокруг `Document(path)` (см. #4) |
| 7 | 1 extractor | `.txt` в UTF-16 (блокнот Windows, «Юникод») → `utf-8-sig` падает, тихий откат на `cp1251` даёт моджибейк; ИНН/телефон не детектируются и утекают искажёнными | **КРИТИЧНО** (тихая порча + утечка ПДн) | FIXED | `test_adversarial_extractor.py::TestUtf16TxtSilentCorruption::test_utf16_txt_preserves_readable_text` | Пробел в спеке (UTF-16 не упомянут; описан только utf-8-sig→cp1251) | `_extract_txt` читает байты и детектирует BOM UTF-16 (`FF FE`/`FE FF`) → декод через `utf-16` ДО отката на cp1251 |
| 8 | 5 session_store | `key.bin` удалён между save и load → `load_session` МОЛЧА пересоздаёт новый ключ; все прочие живые сессии в директории становятся нерасшифровываемыми | **ВАЖНО** (потеря данных без ошибки) | PASS (pins) | `...TestKeyDeletedBetweenSaveAndLoad::test_load_after_key_deletion_silently_recreates_key` | Пробел в спеке (пересоздание ключа на чтении не описано) | — (PASS pins, не трогалось; поведение сохранено при фиксе #1) |
| 9 | 7 CLI | `encrypt` на битом `.docx` печатает сырой `Traceback`/`PackageNotFoundError` вместо `Ошибка: …`; код возврата 1 совпадает случайно | **ВАЖНО** (нарушен контракт вывода, утечка внутренностей) | FIXED | `test_adversarial_cli.py::TestEncryptCorruptDocx::test_corrupt_docx_prints_localized_error_not_traceback` | Противоречит контракту блока 7 (все ошибки — `Ошибка: …`, код 1) | Исправлено фиксом #4 (без изменений в блоке 7): `extract` теперь кидает `ValueError`, который `cmd_encrypt` уже ловит |
| 10 | 7 CLI | Честно истёкшая сессия: `cmd_decrypt` зовёт `purge_expired` (удаляет `.enc`) ДО `load_session` ⇒ пользователь видит «сессия не найдена» вместо «сессия истекла»; ветка `except SessionExpiredError` недостижима (мёртвый код) | **ВАЖНО** (неверный результат/сообщение без падения) | FIXED | `test_adversarial_cli.py::TestDecryptExpiredSessionMessage::test_expired_session_reports_expired_not_not_found` | Противоречит приёмке блока 5 («истёкшая → `SessionExpiredError`») | В `cmd_decrypt` вызов `purge_expired()` перенесён ПОСЛЕ `detokenize`, чтобы сессия сначала загрузилась и истечение было распознано |
| 11 | 5 session_store | Граница TTL включительна: `ttl_hours=0` (`expires_at == created_at`) ⇒ сессия истекла мгновенно (`>=`) | НЕЗНАЧИТЕЛЬНО | PASS (pins) | `...TestTtlBoundaryInclusive::test_ttl_zero_is_immediately_expired` | Пробел в спеке (включительность границы не задана) | — |
| 12 | 4 tokenizer | Entity с `end > len(segment.text)` → `_render_segment` МОЛЧА срезает хвост текста, исключения нет | НЕЗНАЧИТЕЛЬНО (в MVP апстрим даёт корректные оффсеты) | PASS (docs) | `test_adversarial_tokenizer.py::TestEntityOutOfSegmentBounds` | Спека блока 4 явно: предусловие не проверяется, «нарушение = тихая порча» | — |
| — | 6 detokenizer | Токен, разорванный переносом строки (`[ORG_\n1]`), тихо теряется, соседние токены и текст целы, функция не падает | — (подтверждение) | PASS (спека OK) | `test_adversarial_detokenizer.py::TestTokenBrokenByNewline` | Совпадает с «Известным ограничением» блока 6 | — |
| — | 4 tokenizer | Цепочки пересечений из 4 и 5 звеньев и «regex режет двух соседних NER» разрешаются без наложений, итог детерминирован | — (подтверждение) | PASS (спека OK) | `test_adversarial_tokenizer.py::TestLongOverlapChains`, `TestIdenticalSpanDifferentType` | Соответствует алгоритму блока 4 | — |
| — | 5 session_store | `session_id` как атака: path traversal (`../`, `..\`), пустой, юникод, слишком длинный, невалидный hex — все отбиты чистым `SessionNotFoundError`, ФС не тронута | — (подтверждение защиты) | PASS (спека OK) | `test_adversarial_session_store.py::TestSessionIdAttackVectorsBlocked` | Соответствует защите блока 5 (regex `^[0-9a-f-]{36}$`) | — |

## Итог по хрупкости блоков (куда смотреть фикс-этапу в первую очередь)

1. **Блок 5 (session_store) — самый хрупкий и самый опасный.** Три из четырёх КРИТИЧНЫХ
   находок связаны с управлением `key.bin`: гонка при его создании (#1), крах на
   повреждённом ключе (#2, #3) и тихое пересоздание при удалении (#8). Общий корень —
   `_load_or_create_key` не атомарен, не валидирует содержимое ключа и молча создаёт
   ключ даже на пути чтения. Любой из трёх сценариев ведёт к безвозвратной потере
   токен-маппинга без единого исключения. Это первый кандидат на фикс: атомарное
   создание ключа (эксклюзивное создание файла / файловый лок), явная проверка длины
   ключа с обёрткой в `SessionNotFoundError`, и запрет пересоздавать ключ внутри
   `load_session`/`purge_expired`.

2. **Блок 1 (extractor) — вторая болевая точка, и по крахам, и по тихой порче.**
   Любой не-настоящий `.docx` (переименованный, битый, пустой) роняет пайплайн
   неперехваченным `PackageNotFoundError` (#4–#6), а UTF-16-файл (#7) — самый коварный:
   он не падает, а тихо превращает ПДн в моджибейк, из-за чего они не анонимизируются.
   Фикс: обернуть `Document(path)` в понятный `ValueError`, а для `.txt` — детектировать
   UTF-16 по BOM до отката на cp1251 (или хотя бы не откатываться молча).

3. **Блок 7 (CLI) наследует хрупкость блоков 1 и 5** и добавляет свою:
   `purge_expired` перед `load_session` (#10) превращает «истекло» в «не найдено» и
   делает обработчик `SessionExpiredError` мёртвым кодом, а необёрнутые исключения
   блока 1 (#9) вываливаются трейсбеком. Порядок вызовов в `cmd_decrypt` и набор
   перехватываемых исключений в `cmd_encrypt` стоит пересмотреть вместе с фиксами блоков 1 и 5.

4. **Блоки 4 (tokenizer) и 6 (detokenizer) оказались устойчивыми.** Разрешение
   пересечений держит инвариант на цепочках 4–5 звеньев, детокенизатор ведёт себя ровно
   как обещает спека на искажённых LLM-токенах, а защита `session_id` от path traversal
   работает. Единственная оговорка по блоку 4 (#12) — неотключаемое предусловие оффсетов,
   которое спека сознательно оставила непроверяемым; в MVP оно не стреляет, но при
   добавлении новых детекторов в фазе 2 отсутствие защитной проверки станет источником
   тихой порчи текста.
