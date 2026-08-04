# HANDOFF — Блок 5: Хранилище сессии

> **Примечание аудита (2026-07-12).** Это исторический документ сдачи блока: он описывает
> состояние на момент сдачи и НЕ обновляется. Источник истины — `docs/archive/SHIFRATOR_SPEC_AI.md`
> (+ `docs/archive/specs/SHIFRATOR_SPEC_FILE_DECRYPT.md` для блоков 8–12) и `HANDOFF_CURRENT.md` в корне.
> После структурирования проекта все модули лежат в `src/` (импорты остались плоскими).
> **Что здесь устарело (B6, `docs/archive/reports/BREAKING_REPORT.md`):** `delete_session` теперь
> удаляет ОБА файла сессии — `.enc` и сайдкар `.txt`; `purge_expired` при удалении
> просроченного `.enc` удаляет и его `.txt`. Описание «удаляет только .enc» ниже — старое.

## Что сделано
Реализован модуль `session_store.py` — зашифрованное дисковое хранилище токен-маппинга. `save_session` дедуплицирует Entity по `token`, кладёт `{token, entity_type, original_text, segment_id}` в JSON, шифрует его `cryptography.fernet.Fernet` и пишет в `{storage_dir}/{session_id}.enc`; ключ генерируется при первом сохранении в `{storage_dir}/key.bin`. `load_session` валидирует формат id, расшифровывает и проверяет TTL в UTC. `purge_expired` удаляет только просроченные `.enc`, не трогая `key.bin` и нераспознанные файлы. Исключения `SessionNotFoundError`/`SessionExpiredError` определены в этом же модуле. Все сценарии приёмки проверены эмпирически (см. «Данные для тестов»).

**Обновление (этап 2).** Создание `key.bin` сделано атомарным (`_load_or_create_key`: запись во временный файл + публикация через `os.link`, эксклюзивно) — параллельные `save_session` на свежую директорию больше не перезаписывают ключ друг друга. Повреждённый `key.bin` теперь не роняет процесс: `load_session` превращает `ValueError` из `Fernet()` в `SessionNotFoundError`, `purge_expired` — печатает предупреждение и возвращает `0`. У `purge_expired` появился параметр `exclude_session_id` (файл этой сессии не удаляется в проходе — нужно CLI `decrypt`). Добавлена функция `delete_session(session_id, storage_dir=None) -> bool` для ручного удаления одной сессии.

## Публичный интерфейс
dataclass'ы импортируются из `models.py`, не переопределены.

Модуль: `session_store.py`

```python
def save_session(entities: list[Entity], session_id: str | None = None,
                 ttl_hours: int = 24, storage_dir: str | None = None) -> str
def load_session(session_id: str, storage_dir: str | None = None) -> dict
def purge_expired(storage_dir: str | None = None,
                  exclude_session_id: str | None = None) -> int
def delete_session(session_id: str, storage_dir: str | None = None) -> bool

class SessionNotFoundError(Exception): ...
class SessionExpiredError(Exception): ...
```

`exclude_session_id` (по умолчанию `None`) — если задан, файл `{exclude_session_id}.enc`
не удаляется в этом проходе `purge_expired`, все остальные просроченные вычищаются;
нужен CLI `decrypt`, чтобы после очистки отличить «истекла» от «не найдена».
`delete_session` — ручное удаление одной сессии: `True` если файл был и удалён,
`False` если файла не было или `session_id` невалиден по формату; `key.bin` не трогает.

Строки импорта:
```python
from session_store import save_session, load_session, purge_expired, delete_session
from session_store import SessionNotFoundError, SessionExpiredError
```

Минимальный пример вызова:
```python
from session_store import save_session, load_session
# entities — выход блока 4 (list[Entity] с непустым token)
sid = save_session(entities)            # -> str, uuid4; storage_dir=None => ~/.shifrator/sessions
session = load_session(sid)             # -> dict вида «Формат файла сессии»
tokens = session["entities"]            # list[{token, entity_type, original_text, segment_id}]
```

Исключения:
- `ValueError` (stdlib) — `save_session`, если явно переданный `session_id` не matchит `^[0-9a-f-]{36}$`.
- `SessionNotFoundError` (`session_store`) — `load_session`, если `session_id` неверного формата (проверка до обращения к ФС — защита от path traversal), либо файла нет, либо файл не расшифровывается ключом, **либо сам `key.bin` повреждён** (невалидный ключ: `ValueError` из `Fernet()` перехватывается и оборачивается в `SessionNotFoundError`, а не пробрасывается наружу).
- `SessionExpiredError` (`session_store`) — `load_session`, если `expires_at` уже наступил (сравнение в UTC).
- `save_session`/`load_session` могут пробросить `OSError` при проблемах записи/чтения диска.

## Формат данных на выходе
`load_session` возвращает dict ровно вида «Формат файла сессии» из спецификации. Реальный пример:
```json
{
  "session_id": "77a03f96-c8e2-4e7c-b9d8-fd5a0bbf4dd3",
  "created_at": "2026-07-12T03:20:11.482913+03:00",
  "expires_at": "2026-07-13T03:20:11.482913+03:00",
  "entities": [
    {"token": "[ORG_1]", "entity_type": "ORG", "original_text": "ООО «Ромашка»", "segment_id": "p3"},
    {"token": "[INN_1]", "entity_type": "INN", "original_text": "7701234567", "segment_id": "p3"}
  ]
}
```
`save_session` возвращает `session_id: str` (сгенерированный uuid4, если на вход пришёл `None`).
`purge_expired` возвращает `int` — число фактически удалённых просроченных `.enc`-файлов (при `exclude_session_id` файл исключённой сессии в это число не входит и не удаляется).
`delete_session` возвращает `bool` — `True`, если файл сессии существовал и удалён; `False`, если файла не было или `session_id` невалиден по формату.

## Инварианты выходных данных
Ключи dict из `load_session` (гарантированно присутствуют все): `session_id` (str), `created_at` (str, ISO 8601 timezone-aware), `expires_at` (str, ISO 8601 timezone-aware), `entities` (list, возможно пустой).
Каждая запись в `entities` содержит ровно 4 ключа: `token` (str, непустой), `entity_type` (str), `original_text` (str), `segment_id` (str).
- **Одна запись на токен**: дедупликация по `token`, повторов нет.
- **Порядок** записей в `entities` = порядок первого появления токена во входном `list[Entity]` (блок 4 отдаёт список, отсортированный по `(индекс сегмента, start)`, — этот порядок сохраняется). При дедупликации сохраняется `segment_id` первого вхождения.
- `created_at <= expires_at`; `expires_at - created_at == ttl_hours`.

## Что блок ожидает на входе (предусловия)
- Каждый входной `Entity.token` — непустая строка (инвариант блока 4: `all(e.token for e in result)`). Блок это **не проверяет**; если `token is None`, записи с `None`-токеном схлопнутся в одну и попадут в файл с `"token": null` — тихая порча, не исключение.
- Entity с одинаковым `token` имеют одинаковые `entity_type`/`original_text` (гарантия блока 4). Блок берёт эти поля у первого вхождения токена и не сверяет остальные.
- `ttl_hours` — положительное число (не проверяется; при <=0 сессия создаётся уже просроченной).

## Использованные поля конфига
Блок `entity_types.yaml` **не читает** — работает только с готовыми Entity. Полей конфига не использует.

## Побочные эффекты импорта
`import session_store` подтягивает `cryptography.fernet` (быстрый импорт, без загрузки моделей), stdlib и `models`. Директории/файлы при импорте **не создаются**, диск не читается. `~/.shifrator/sessions` и `key.bin` создаются лениво — только при первом `save_session` (или при `load_session`, где отсутствующий `key.bin` был бы создан пустым ключом; на практике load идёт после save). Модуль можно импортировать лениво в ветке, где сессии не нужны.

## Зависимости и окружение
- `cryptography` (установлен в `venv`; версия — актуальная с PyPI на момент реализации).
- Python: проверено на выводе `python --version` интерпретатора `venv/Scripts/python.exe` (проект зафиксирован на 3.11; фактически стек работает и на 3.12).
- Проверочного запуска импорта сторонней NER-библиотеки (как в блоке 3) здесь не требуется.

## Соглашения о путях и файлах времени выполнения
- Дефолтная директория хранилища (`storage_dir=None` во всех трёх функциях): `~/.shifrator/sessions`. Создаётся автоматически при первом сохранении.
- Файл ключа: `{storage_dir}/key.bin` (Fernet-ключ, генерируется при первом save, паролем не защищён — ограничение MVP). Создаётся атомарно: временный файл `key.bin.*.tmp` в той же директории + публикация через `os.link`. Временный файл затем удаляется, но код, перечисляющий содержимое директории, должен быть готов увидеть его мельком. Атомарность полная на POSIX и Windows/NTFS; на ФС без жёстких ссылок — запасной неатомарный путь (`O_CREAT|O_EXCL`).
- Файлы сессий: `{storage_dir}/{session_id}.enc`.
- Права `0o600` выставляются на `key.bin` и `*.enc` **только на POSIX**; на Windows (текущее окружение разработки) не действуют — ограничение MVP, как и предписано спецификацией.

## Отклонения от спецификации
Отклонений нет.

## Предложения по изменению интерфейсов
Нет.

## Известные ограничения / TODO
- `key.bin` не защищён паролем / OS keyring — защита только от случайного прочтения посторонним ПО, не от локального злоумышленника с доступом к диску (фаза 2).
- Права `0o600` на Windows не применяются (нет POSIX-прав) — ограничение MVP.
- `purge_expired` при отсутствии `key.bin` в директории возвращает 0 (расшифровать TTL нечем) — файлы не удаляются, что безопасно. То же при **повреждённом** `key.bin`: предупреждение в stderr + `return 0`, без падения.
- Ручное удаление сессии из-под `decrypt`-очистки: если пользователь запросил `decrypt` для истёкшей сессии, её `.enc` НЕ удаляется в этом проходе (исключён через `exclude_session_id`), чтобы показать «истекла»; он будет вычищен при следующей очистке, когда перестанет быть запрошенным.

## Данные для тестов
1. **Дедупликация + save/load.** Вход: 3 Entity — два с `token="[ORG_1]"` (`segment_id` `p3` и `p7`) и один `"[INN_1]"` (`p3`). Ожидаемо: в файле 2 записи; у `[ORG_1]` сохранён `segment_id="p3"` (первое вхождение). ✅ проверено.
2. **Приёмка: новый процесс + дефолтная директория.** `save_session(e)` в одном процессе → `load_session(sid)` в новом процессе интерпретатора без `storage_dir` → данные идентичны, файл найден по `~/.shifrator/sessions`. ✅ проверено.
3. **Истёкший TTL (граничный).** Ручная подмена `expires_at` на прошедшую дату → `load_session` кидает `SessionExpiredError`. ✅ проверено.
4. **purge_expired избирательность.** Директория с `key.bin`, `garbage.txt`, `broken.enc` (не-Fernet) и одним просроченным `.enc` → удаляется ровно 1 файл (просроченный `.enc`); `key.bin`, `garbage.txt`, `broken.enc` остаются, живая сессия по-прежнему загружается. ✅ проверено.
5. **Валидация формата.** `save_session([], session_id="../evil")` → `ValueError`; `load_session("../../etc/passwd")` → `SessionNotFoundError` без обращения к ФС; пустой список entities → сессия создаётся с `"entities": []`. ✅ проверено.
6. **purge_expired с exclude (этап 2).** Две просроченные сессии A (запрошенная) и B (чужая) → `purge_expired(exclude_session_id=A)` удаляет только B (`removed == 1`), файл A остаётся, `load_session(A)` затем кидает `SessionExpiredError`. Дефолт `exclude_session_id=None` — прежнее поведение (удаляются все просроченные). ✅ проверено (`tests/test_session_store.py::TestPurgeExpiredExcludeSession`).
7. **delete_session (этап 2).** Существующая сессия → `True`, `.enc` удалён; отсутствующая (валидный формат) → `False`; невалидный формат (`../../etc/passwd`) → `False` без обращения к ФС; `key.bin` не тронут ни в одном случае. ✅ проверено (`tests/test_session_store.py::TestDeleteSession`).

## Файлы блока
- `session_store.py` (создан)
- `requirements.txt` — уже содержал `cryptography`; пакет установлен в `venv`.
