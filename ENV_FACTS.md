# ENV_FACTS — факты окружения для внешнего анализа

Собрано чтением файлов/метаданных, без pytest/gate.py/harness. Дата: 2026-08-04.
«НЕ НАЙДЕНО» — нет источника.

## 1. Зависимости

| Пакет | Версия (venv) | Лицензия | Для чего |
|---|---|---|---|
| natasha | 1.6.0 | MIT | NER/морфология/синтаксис ([requirements.txt:3](requirements.txt:3)) |
| navec | 0.10.0 (транзит.) | MIT | эмбеддинги natasha |
| slovnet | 0.6.0 (транзит.) | MIT | нейросети natasha (NER/morph/syntax) |
| pymorphy2 | 0.9.1 | MIT | морфология ([requirements.txt:4](requirements.txt:4)) |
| pymorphy2-dicts-ru | 2.4.417127.4579844 | MIT | словарь ru |
| yargy | 0.16.0 | MIT | грамматика (адрес, деньги, [requirements.txt:5](requirements.txt:5)) |
| razdel | 0.5.0 (транзит.) | MIT | токенизация |
| python-docx | без пина; 1.2.0 в venv | MIT | .docx ([requirements.txt:1](requirements.txt:1)) |
| python-pptx | 1.0.2 (транзит.) | MIT | .pptx rewriter |
| openpyxl | 3.1.5 (транзит.) | MIT | .xlsx rewriter |
| PyYAML | без пина; 6.0.3 | MIT | `entity_types.yaml` ([requirements.txt:2](requirements.txt:2)) |
| cryptography | без пина; 49.0.0 | Apache-2.0 OR BSD-3-Clause (файлы `LICENSE.APACHE`/`LICENSE.BSD` в dist-info) | Fernet-шифрование сессий |
| lxml | 6.1.1 (транзит.) | BSD-3-Clause | разбор OOXML |
| numpy | 2.4.6 (транзит.) | нет Classifier; файл лицензии в dist-info есть, текст не читан — BSD-3 публично известна, но здесь ДОГАДКА |
| pyinstaller | 6.21.0 | GPLv2-or-later + исключение на сборку несвободных программ | упаковка exe |

Источники: `pip freeze`/`pip show`/`*.dist-info/`. **Вес моделей на диске** (`du -sh`):
navec `.tar` 26 МБ; `slovnet_morph/ner/syntax` по 2.3–2.5 МБ каждый;
`natasha/data/dict/{first,last,maybe_first}.txt` — без общего замера;
`pymorphy2_dicts_ru/` целиком — 15 МБ.

## 2. Поставка (`packaging/shifrator.spec`)

- `--onedir`, не `--onefile` — против распаковки при каждом запуске ([packaging/shifrator.spec:1-9](packaging/shifrator.spec:1)).
- `console=False`, статус через tkinter ([packaging/shifrator.spec:158-163](packaging/shifrator.spec:158)); точка входа `app/launcher.py` ([packaging/shifrator.spec:141](packaging/shifrator.spec:141)).
- Целевая платформа в спеке не объявлена явно; фактически собрано под Windows (`.exe`). Требований к правам администратора в спеке НЕТ; по приёмке — не требуются (см. §3).
- Data-файлы: веса natasha (`collect_data_files`), `pymorphy2_dicts_ru` + его dist-info метаданные, `entity_types.yaml`, `app/index.html` ([packaging/shifrator.spec:96-103](packaging/shifrator.spec:96)).
- Сборка сверяет версии критичных пакетов с `packaging/requirements-build.lock.txt`, падает при расхождении ([packaging/shifrator.spec:44-84](packaging/shifrator.spec:44)); python сборки 3.11.9 AMD64 win32 ([packaging/requirements-build.lock.txt:16](packaging/requirements-build.lock.txt:16)).
- Последняя папка на диске: `dist/SHIFRATOR/` — 128 МБ, 1108 файлов, метка `build-20260729` (`e92e8a6`, [docs/JOURNAL.md:118-127](docs/JOURNAL.md:118)). Также `dist/SHIFRATOR_prev_u1b/` — 128 МБ. Прошлый точный замер той же поставки: 130 983 556 Б = 124.9 МиБ, 1108 файлов ([docs/archive/reports/HANDOFF_U1b_REBUILD.md:227](docs/archive/reports/HANDOFF_U1b_REBUILD.md:227)) — расхождение с текущим `du -sh` в пределах округления единиц.

## 3. Время старта и обработки (только зафиксированные цифры)

| Показатель | Значение | Источник |
|---|---|---|
| Импорт стека детекции, один раз | 1.24–1.44 с (сред. 1.31 с) | [docs/archive/reports/PERF_REPORT.md:55](docs/archive/reports/PERF_REPORT.md:55), Win11 12 ядер |
| Обработка документа, среднее/медиана | 1.43 с / 1.26 с | [docs/archive/reports/PERF_REPORT.md:57-59](docs/archive/reports/PERF_REPORT.md:57) |
| Самый дорогой/дешёвый документ | 3.35 с / 0.69 с | [docs/archive/reports/PERF_REPORT.md:110-112](docs/archive/reports/PERF_REPORT.md:110) |
| Полный гейт (324 док.) ДО этапа SPEED | 462–604 с | [docs/archive/reports/PERF_REPORT.md:57](docs/archive/reports/PERF_REPORT.md:57) |
| Полный гейт ПОСЛЕ этапа SPEED | **152 с** | [docs/STATE.md:22-25](docs/STATE.md:22), 2026-08-04 |
| pytest полный | 765.9 с, 1334 passed/3 skipped/3 deselected/10 xfailed | [docs/STATE.md:18-19](docs/STATE.md:18), этап SPEED |
| Холодный старт exe (диск прогрет) | 1.66/1.79/1.80 с | [docs/archive/reports/HANDOFF_U1b_REBUILD.md:227](docs/archive/reports/HANDOFF_U1b_REBUILD.md:227), сборка `u1b-rebuild-20260725` |
| Старт свежераспакованной копии | 3.78 с | -"- |
| Сборка exe с нуля | 41 с | -"- |
| Память воркера natasha | ~296 МБ после импорта, ~409 МБ после 33 док. | [docs/STATE.md:31-34](docs/STATE.md:31), `tasklist` |

`build-20260729` — только смена метки, без изменений программы ([docs/JOURNAL.md:119-127](docs/JOURNAL.md:119)): цифры старта/размера выше сняты с ПРЕДЫДУЩЕЙ сборки `u1b-rebuild-20260725`.

## 4. Метрики — сведение

**Единой цифры recall по агрегату НЕТ.** Основная метрика гейта — `leak_v2`
(частичная утечка в масках), не recall. Цифра **«82% recall» НЕ НАЙДЕНА**
нигде в `docs/STATE.md`, `docs/FINDINGS.md`, `docs/JOURNAL.md`.

| Метрика | Значение | Корпус/этап | Источник |
|---|---|---|---|
| leak_v2 агрегат ≥6 / ≥8 | 18,7 % / 17,2 % | v1 324 док., SPEED 2026-08-04 | [docs/STATE.md:43](docs/STATE.md:43) |
| masking B (границы) агрегат | 91,60 % | v1, SPEED | [docs/STATE.md:44](docs/STATE.md:44) |
| masking A (round-trip) | 100,00 % | v1, SPEED | [docs/STATE.md:42](docs/STATE.md:42) |
| PERCENT / TERM recall | 96,5 % / 95,1 % | v2, T2 2026-08-04 | [docs/STATE.md:77](docs/STATE.md:77) |
| SUM recall | 97,5 % (canonical 100 %, вся потеря — adversarial) | v2, 648 сущ., T-GOLD-A | [docs/FINDINGS.md:15](docs/FINDINGS.md:15) |
| PASSPORT recall_exact | 0 % (loose 26,1→30,8 % после этапа 2) | до этапа 2 | [docs/FINDINGS.md:20](docs/FINDINGS.md:20) |
| ADDRESS recall под мутацией | 87–89 % (было 93,6 %) | Stage-C-A | [docs/FINDINGS.md:22](docs/FINDINGS.md:22) |
| ORG recall | 93,9→94,2 % | этап B | [docs/FINDINGS.md:29](docs/FINDINGS.md:29) |

Старые цифры (55,9 %, 68 % и т.п., этап 2 и ранее) в текущих `docs/*.md` не встречаются — не приведены как устаревшие.

## 5. Открытые долги (`docs/FINDINGS.md` + `STATE.md` §6)

| id | суть | тип | страж |
|---|---|---|---|
| TGOLDA-SUM-CASE | `SUM` без `(?i)`, регистр рвёт матч на adversarial | полнота | нет |
| Stage3-A | BIRTHDATE: недобор 1 симв. при омоглифе цифры | полнота, незнач. | нет |
| ADDRB-BIRTHDATE-EXPOSED | дата рождения с омоглифами не детектируется и не прикрыта | безопасность | `test_addr_b_boundaries.py::…` xfail(strict) |
| ADDRB-PRECISION-FRAG, ADDRB-NL-HOLE | precision считается в масках (склейка спанов выглядит падением); `\n` между масками засчитан «короче эталона» | артефакты метрики | нет |
| 0c-B | PASSPORT recall_exact 0% | полнота | нет |
| Stage-C-A | ADDRESS под мутацией −4.7пп, остаток 43 | полнота | `docs/known_leaks_stage_c.json` |
| Stage-C-B | паспорт+гомоглиф → ложный ADDRESS (7 сл.) | точность, незнач. | нет |
| 1-A | «отказано» смешано с «упало» в харнессе | учёт, латентно | нет |
| 1-B | `w:sdt` верхнего уровня не читается extractor'ом | безопасность | нет |
| Stage4-B | PHONE не детектируется при гомоглиф-мутации (2 док.) | безопасность | нет |
| Stage2b-A | регистр×омоглиф теряет ФИО на combo | полнота | `test_case_detection.py::…` |
| B-MASKSHIFT-ORG | ORG без Natasha-PER больше не маскируется случайно | безопасность | нет |
| Aprime-1 | ORG без якоря уходит в PERSON | тип неверный | нет |
| EPRIME-A-CONFIRM | недетерминизм устранён механизмом, не подтверждён на реальном документе | детерминизм | `test_determinism.py` (механизм) |
| Eprime-B | остаток утечки ADDRESS после E′ (43) | полнота | `docs/known_leaks_stage_c.json` |
| V2-TRANCHE-NOGUARD | запрет TRANCHE без автостража (ручной `validate.py`) | процесс | нет |
| V2-WALL-NOGUARD | стена `src/`↔корпус v2 без исполняемого стража | процесс | нет |
| E2-CORPUS-POVERTY | корпус (2 стиля/док.) беднее реальных (1097 стилей) | покрытие | частично: `synthetic_many_styles.docx` |
| STATE §6.4 / §6.5 | `experiments/` не разобран; yargy — 73% времени, резерв производительности | гигиена/производительность | нет |
| Stage4-C, B-ADDR-HOMONYM, Aprime-2/3, Stage-Cprime-A/B | точечные незначительные находки (КС ОГРНИП случайное совпадение, улица-однофамилец под PER, латинский омоглиф org-формы, `\n`↔пробел асимметрия в таблицах, усечённый ORG-фрагмент/втянутое ФИО в реквизитной ветви) — по 1 документу каждая, полный текст в `docs/FINDINGS.md` | точность, все незнач. | нет |
| Stage-E-A | цепочка цифр может быть принята за телефон вместо ИНН/паспорта (направление безопасное) | точность | нет |
| Stage-E-B | `decrypt-file` подставляет форму первого вхождения, не канон | корректность формы | нет |

## 6. Хранение на диске (`~/.shifrator/`, по `src/session_store.py`)

| Файл | Шифруется | TTL | Удаление | Права/DPAPI |
|---|---|---|---|---|
| `key.bin` | нет (сам ключ Fernet) | бессрочно | `purge_expired` его не трогает ([src/session_store.py:303](src/session_store.py:303)) | `chmod 0600` на POSIX; на Windows — ничего ([src/session_store.py:60-66](src/session_store.py:60)) |
| `{sid}.enc` | да, `cryptography.fernet` ([src/session_store.py:226-227](src/session_store.py:226)) | 24 ч по умолчанию (`ttl_hours=24`, [src/session_store.py:216](src/session_store.py:216)) | `purge_expired()` авто в начале `decrypt` для чужих просроч. ([shifrator.py:190](shifrator.py:190)); `delete_session()` вручную | `chmod 0600` POSIX, Windows — нет ([src/session_store.py:235](src/session_store.py:235)) |
| `{sid}.txt` (анонимизир. сайдкар) | не отдельно (текст уже токенизирован) | следует за `.enc` | вместе с `.enc` (B6-fix, [src/session_store.py:369-370](src/session_store.py:369)) | не упомянуто |

Директория по умолчанию: `Path.home() / ".shifrator" / "sessions"` ([src/session_store.py:35](src/session_store.py:35)).
**DPAPI не используется** — только Fernet; вызовов `CryptProtectData`/DPAPI в `src/`/`app/` нет (см. §7 grep).
`decrypt-file` НЕ вызывает `purge_expired` ([shifrator.py:226-227](shifrator.py:226)).

## 7. Сеть

Команда: `grep -rn "socket|requests\.|urllib\.request|http\.client|telemetry|urlopen" src/ app/`

`src/`: 0 совпадений. `app/launcher.py:41,44` — `urllib.request.urlopen("http://127.0.0.1:{port}/api/ping")` (проверка локального сервера). `app/server.py:13,46,54` — `socket.socket(AF_INET, SOCK_STREAM)`, `http.server` слушает `127.0.0.1`. Внешних соединений (интернет, телеметрия, обновления) не обнаружено.
