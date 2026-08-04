# WERT_CLEANUP — удаление wert.docx из истории git

Дата операции: 2026-07-28.
Контекст: docs/archive/reports/REAL_DOCS_CHECK.md зафиксировал, что в истории репозитория
остался реальный документ `wert.docx` (коммит `daaef29e`), не затронутый
прошлой чисткой `git filter-repo` (проводилась 2026-07-20, цель —
`real_docs/` и `dogovor.docx`).

## Резервная копия

Путь: `C:\ARP_backup_20260728_182204`

Проверка перед операцией:
- размер оригинала и копии совпадает: `5.0G` / `5.0G` (`du -sh`)
- `.git/HEAD` в копии существует и читается: `ref: refs/heads/main`

## Шаг 2 — фиксация висящих изменений

`git status --porcelain` до операции показывал изменённые
`experiments/stage_o2/acceptance.py`, `experiments/stage_o2/etalon.py`,
`tests/corpus/subsample.py` и новые файлы `docs/archive/specs/ENTITY_SPEC.md`,
`docs/archive/reports/GENV_REPORT.md`, `docs/archive/reports/HANDOFF_SUBSET_ITER.md`,
`docs/archive/specs/MARKUP_RULES.md`, `docs/archive/reports/PERF_REPORT.md`, `docs/archive/reports/T0V_REPORT.md`.

Закоммичено двумя коммитами (хеши изменились после filter-repo, см. ниже
текущие хеши после операции):
1. `c1b8ef3` — «Обновление acceptance/etalon (stage_o2) и subsample.py
   тестового корпуса»
2. `14de913` — «Добавлены отчёты: ENTITY_SPEC, GENV, HANDOFF_SUBSET_ITER,
   MARKUP_RULES, PERF, T0V»

### Стеши

До операции (`git stash list`):
```
stash@{0}: On stage-eprime-determinism: WIP determinism investigation before switching to u1-desktop-packaging for U2 task
stash@{1}: On stage-eprime-determinism: WIP owner_repro.py before U1 packaging branch
```

После коммитов шага 2 (перед filter-repo) — список идентичен, оба стеша
на месте.

После filter-repo — filter-repo сообщил `Rewrote the stash.`; оба стеша
сохранились (filter-repo переписывает стеши так же, как ветки, а не
теряет их — WIP из стешей не пострадал).

## Шаг 3 — проверка перед операцией

`git show daaef29e:wert.docx > NUL` — отработала успешно, exit code 0,
файл прочитан (510935 байт).

`git log --all --oneline -- wert.docx`:
```
8ebb187 Блоки 8,9 с легкими тестами
daaef29 Исправлен баг с пограничными данными
```

## Промежуточная остановка (ШАГ 0)

Первый запуск `git filter-repo --invert-paths --path wert.docx --force`
прервался: в `.git/filter-repo/` уже лежал маркер `already_ran` от
предыдущей чистки (2026-07-20), и filter-repo запросил интерактивное
подтверждение `Treat this run as a continuation...`, которое в
неинтерактивном режиме привело к `EOFError`. История в этот момент **не
была изменена**.

По запросу владельца выполнена диагностика (только чтение, без
подтверждений и удалений):
- `already_ran` — штатный маркер успешного завершения (текст: «This file
  exists to allow you to filter again without --force, and to specify
  that metadata files should be updated instead of rewritten»), файлы в
  `.git/filter-repo/` датированы 20 июля.
- `git log --all --oneline -- real_docs/ dogovor.docx` — пусто в обоих
  случаях: цель прошлой чистки действительно вычищена.
- `git fsck --full` — 15 dangling-объектов (9 commit, 4 tree, 1 blob, 1
  tree повторно — см. список ниже), ошибок целостности нет (exit 0):
```
dangling commit 5da0792b896e89e3ddcfa0311d42d7a6f0357a9c
dangling tree a9a3b5a4427c90fee9c6c9821ada2b5125177eac
dangling commit b1e32b6c299c55f89e5f02776a24d7127949a11f
dangling tree ffa4dd45b4f4e40d637d0e2cf3b842ae130cc221
dangling tree 2346c4acc15f39a4ecff2eed67453f21ed0b5f30
dangling commit 034b6c1a2bb549c83fd8b737507dbea4a2f9440f
dangling commit 236bba8cd5a167fdac4fd55893eeeafbc8d11099
dangling commit 69cbe76959eb96208492ea960a8290b6bc2635ed
dangling commit 906e9e082acf8dc3c9e73bed3eb74567da7fc2d5
dangling blob 11af18cdd51d4761922cb21ad285b01456bdce77
dangling commit c9506d58e2b69c716d15296d7b9524f93659e9d8
dangling tree b6b4684d121c19ff3382b60b19c6e0dcdc6d49fc
dangling commit 431942bda5eda90df9201ce2d975a0eba7231da3
dangling commit cada60c048b47fa7287433c3d48030d506389dba
dangling commit d89ed5d69699b75947938986ffbba671614f2f65
```
- `git count-objects -v`: `count: 1690, size: 14369, in-pack: 1351,
  packs: 1, size-pack: 6043, prune-packable: 0, garbage: 0,
  size-garbage: 0`

Владелец подтвердил: прошлая чистка завершена штатно, продолжаем.
Удалён только файл `.git/filter-repo/already_ran` (каталог не тронут),
команда повторена без изменений.

## Шаг 4 — чистка (повторный успешный запуск)

```
python -m git_filter_repo --invert-paths --path wert.docx --force
```

Вывод:
```
NOTICE: Removing 'origin' remote; see 'Why is my origin removed?'
        in the manual if you want to push back there.
        (was https://github.com/fortunaeg-le/ARP.git)
Parsed 1 commitsParsed 89 commitsParsed 100 commits
HEAD is now at 14de913 Добавлены отчёты: ENTITY_SPEC, GENV, HANDOFF_SUBSET_ITER, MARKUP_RULES, PERF, T0V

New history written in 0.59 seconds; now repacking/cleaning...
Repacking your repo and cleaning out old unneeded objects
Rewrote the stash.
Completely finished after 5.17 seconds.
```

**Отклонение от инструкции, не запрошенное явно:** filter-repo в штатном
режиме сам удалил remote `origin`
(`https://github.com/fortunaeg-le/ARP.git`) — это встроенная защита
инструмента от случайного пуша переписанной истории поверх старой на
сервере. Никаких `push` не выполнялось. См. раздел «Удалённый
репозиторий» ниже.

## Шаг 5 — проверки после операции

а) `git log --all --oneline -- wert.docx` → пусто.

б) `git show daaef29e:wert.docx` → `fatal: invalid object name
'daaef29e'.` (exit 128) — старый хеш коммита переписан, объект под этим
именем больше не существует. Это ожидаемый результат.

в) `git rev-list --all --objects | grep -i wert` → пусто.

### Дополнение владельца: гарантированная физическая зачистка

```
git reflog expire --expire=now --all
git gc --prune=now
```

Повтор проверки в) после gc → пусто (без изменений).

`git fsck --full` после gc → **пустой вывод**: ни ошибок, ни единого
dangling-объекта (в том числе исчезли и 15 dangling-объектов, оставшихся
от прошлой чистки 20 июля — `git gc --prune=now` собрал их заодно).

Точечная проверка конкретного блоба: хеш `wert.docx` из `daaef29e` в
резервной копии — `ad28b62ae3a38a73e7ce9d3fe1b53998a5ab98b3`
(`git rev-parse daaef29e:wert.docx` в `C:\ARP_backup_20260728_182204`).
`git cat-file -e ad28b62ae3a38a73e7ce9d3fe1b53998a5ab98b3` в текущем
репозитории → ошибка (exit 1): объект физически отсутствует в
хранилище, не просто отцеплен.

## Шаг 6 — целостность после операции

а) `git status --porcelain` → пусто, рабочее дерево не потеряло файлов.

б) `sha256sum -c tests/corpus/MANIFEST.sha256` (из `tests/corpus/`):
**656 OK, 0 FAILED**. Корпус цел.

в) Число коммитов:
- До операции (резервная копия, `git log --all --oneline | wc -l`,
  все ветки, без двух коммитов шага 2): **95**
- После операции (`git log --all --oneline | wc -l`, все ветки): **97**
- Арифметика сходится: 95 + 2 (коммиты шага 2, добавленные до
  filter-repo) = 97. Ни один коммит не потерян — filter-repo переписал
  деревья коммитов `8ebb187` и `daaef29e` (убрав из них wert.docx), но
  сами коммиты не стали пустыми и не были отброшены, только получили
  новые хеши.

`git log --oneline` (main, топ-10 после операции):
```
14de913 Добавлены отчёты: ENTITY_SPEC, GENV, HANDOFF_SUBSET_ITER, MARKUP_RULES, PERF, T0V
c1b8ef3 Обновление acceptance/etalon (stage_o2) и subsample.py тестового корпуса
3b492fc DOC-REAL-CHECK: проверка репозитория на следы реальных договоров
78cfc34 DOC-LEGAL-2: правка PRODUCT_LEGAL.md по фактам LEGAL_CHECK.md
c7b13b0 DOC-LEGAL-CHECK: фактическая проверка PRODUCT_LEGAL.md (docs/archive/legal/LEGAL_CHECK.md)
75e285c Догонка CORPUS-V2-B: настоящая сверка байтов, возврат «цифры против прописи», слияние номеров, замок на послаблении, TRANCHE помечен неизмеримым
9313e8d Техническое описание продукта для юриста по ИС (РАЗДЕЛ DOC-LEGAL).
a29035e Возврат чужой правки tests/corpus/subsample.py, захваченной по недосмотру
55ea0ba Разрыв форматированием не доходил до файла у номеров договоров
1dedafa Тест воспроизводимости грузил ядро СТАРОГО корпуса
```

## Шаг 7 — удалённый репозиторий

`git remote -v` **до** операции:
```
origin	https://github.com/fortunaeg-le/ARP.git (fetch)
origin	https://github.com/fortunaeg-le/ARP.git (push)
```

`git remote -v` **после** операции: пусто — filter-repo автоматически
удалил `origin` в рамках своей защитной логики (см. вывод шага 4 выше).

**Ничего не запушено.** Удалённый репозиторий `origin` существует
(GitHub, `fortunaeg-le/ARP`) и содержит старую историю с `wert.docx` —
локальная чистка его не затрагивает. Решение о принудительном пуше
переписанной истории (потребуется `git remote add origin ...` и
`git push --force`, что перезапишет историю на сервере и потребует
координации с любыми другими клонами/форками) принимает владелец
репозитория.

## Удалённая сторона (продолжение операции по решению владельца)

Владелец подтвердил push переписанной истории в GitHub `origin`. Перед
push повторно проверены шаги 5–6: `wert.docx` отсутствует в истории и
объектах, `git fsck --full` пуст, MANIFEST корпуса 656/656 OK.
(Единственное совпадение по `grep -i wert` — файл этого же отчёта
`docs/archive/reports/WERT_CLEANUP.md`, ложное срабатывание по имени файла, не сам
документ.)

### 1. Пуш

`git remote add origin https://github.com/fortunaeg-le/ARP.git` — выполнено.

`git push --force --all origin` — **не выполнен**: команда зависла и была
прервана по таймауту (2 минуты, exit 143). Причина: локально настроен
`credential.helper = manager` (Git Credential Manager), который при пуше
пытается открыть интерактивное окно авторизации в браузере — в этой
изолированной сессии у меня нет доступа к такому интерактивному GUI и нет
токена/пароля для аутентификации как `fortunaeg-le` (ввод учётных данных
мной запрещён политикой безопасности в любом случае). После прерывания
проверено: `git status --porcelain` пусто, lock-файлов нет, репозиторий не
повреждён, попытка передачи данных не началась.

`git push --force --tags origin` — не выполнялся (локальных тегов нет,
`git tag` пуст).

**Push не выполнен. Требуется ручной запуск владельцем** (в его терминале,
где GCM сможет открыть окно входа):
```
cd C:\Jesus\ARP
git push --force --all origin
git push --force --tags origin
```

### 2. Старые ветки на сервере

`git ls-remote origin` (до push, т.е. пока ещё старая история на сервере):
```
09a4d545a7f475e43a96ca638473e3a5c6d2fa07	HEAD
09a4d545a7f475e43a96ca638473e3a5c6d2fa07	refs/heads/main
b83803eee5a2826a0fbfb5f93329929c7c09e4b0	refs/heads/stage-c-address
```
Локальные ветки: `main, stage-b-per, stage-c-address,
stage-c-prime-quotes, stage-e-prime-apply, stage-e-spans,
stage-eprime-determinism, u1-desktop-packaging`. Тегов нет ни локально,
ни на сервере.

Сверка: обе серверные ветки (`main`, `stage-c-address`) существуют и
локально — лишних веток, которых нет локально, на сервере **не найдено**.
Ничего не удалялось. (Важная оговорка: поскольку push ещё не выполнен,
`main` и `stage-c-address` на сервере сейчас всё ещё содержат СТАРУЮ
историю со старыми хешами (`09a4d545...`, `b83803ee...`) — они будут
переписаны, когда владелец выполнит push вручную.)

### 3. Жив ли старый коммит daaef29e (GitHub API, без авторизации)

```
curl -s -o /dev/null -w "%{http_code}" https://api.github.com/repos/fortunaeg-le/ARP/commits/daaef29e
→ 404
```
Запрос к самому репозиторию без авторизации тоже вернул 404:
```
curl -s https://api.github.com/repos/fortunaeg-le/ARP
→ {"message": "Not Found", ...}
```
Это типичный ответ и для приватного репозитория, и для реально
отсутствующего объекта — **неинформативно без авторизации**.
`gh` CLI в системе не установлен (`command not found`), токена для
авторизованного запроса у меня нет. **Проверка неинформативна, нужен
вход в браузере** (владельцу — открыть
`https://github.com/fortunaeg-le/ARP/commits` или конкретный хеш
коммита в браузере под своим аккаунтом).

### 4. Старые хеши прошлой чистки (20 июля, real_docs/dogovor.docx)

Текущий `.git/filter-repo/commit-map` в рабочем репозитории относится
только к сегодняшнему запуску (файл `already_ran` был удалён и запуск
начался как новый — карта соответствий от 20 июля была перезаписана).
Старые хеши восстановлены из **резервной копии**
(`C:\ARP_backup_20260728_182204\.git\filter-repo\commit-map` и
`first-changed-commits`), где сохранилась карта 20 июля. Найдено 6
коммитов, изменённых той чисткой: 4 переписанных и 2 полностью обнулённых
(были пусты после удаления `real_docs/`/`dogovor.docx`):

| старый хеш (до чистки 20 июля) | результат |
|---|---|
| `082b2c42835e92a770a83eec8b4bc81298f86080` | переписан |
| `3ef0fdf23dd4dcd73bc47674bc3417b1e716b76f` | переписан |
| `9ad8773485dc595ab67e2ae441bb976ce7d99574` | переписан |
| `f3e65c97351dd1a824fc0dd7e43bc47b12a4c83e` | переписан (упомянут владельцем) |
| `3282dd2764836572240da7864dc475672df8040a` | обнулён (пустой коммит) |
| `643cb5e238555ac71856c82f7fd3256952ef23f8` | обнулён (пустой коммит) |

Проверка всех шести через `api.github.com/repos/fortunaeg-le/ARP/commits/<hash>`
без авторизации — **все 404**, по той же причине неинформативно (репозиторий,
судя по всему, приватный — см. п.1 выше).

### 5. Видимость и форки

```
GET /repos/fortunaeg-le/ARP        → 404 {"message":"Not Found"}
GET /repos/fortunaeg-le/ARP/forks  → 404 {"message":"Not Found"}
```
Без авторизации нельзя определить ни `private`-флаг, ни число форков —
GitHub отдаёт 404 вместо 403 на приватные репозитории намеренно (чтобы не
подтверждать даже факт существования). **Нужен авторизованный запрос**
(токен или вход в браузере) для содержательного ответа.

### 6. Релизы и pull request'ы

```
GET /repos/fortunaeg-le/ARP/releases          → 404 {"message":"Not Found"}
GET /repos/fortunaeg-le/ARP/pulls?state=all   → 404 {"message":"Not Found"}
```
Аналогично — неинформативно без авторизации.

## Итог

- `wert.docx` физически удалён из истории и из object store (проверено
  точечно по хешу блоба) — **локально**.
- Прошлая чистка (`real_docs/`, `dogovor.docx`) осталась нетронутой и
  подтверждена повторно как выполненная — **локально**.
- Корпус тестов цел (656/656 по MANIFEST).
- Рабочее дерево не пострадало.
- Оба рабочих стеша сохранены.
- `.gitignore` не изменялся.
- Ничего не удалялось на сервере (веток, релизов, PR, форков) — только
  чтение через API.

**Что удалось сделать своими силами:** полная зачистка `wert.docx` из
локального репозитория, физическое удаление объекта, подтверждение через
`fsck`/`rev-list`/`gc`, целостность корпуса и рабочего дерева, сверка
списка веток на сервере (лишних не найдено), извлечение старых хешей
прошлой чистки из резервной копии.

**Что требует действий владельца / поддержки GitHub:**
1. Сам push (`git push --force --all origin` и `--tags`) — заблокирован
   отсутствием интерактивной авторизации в этой сессии, нужно запустить
   вручную в браузере/терминале владельца.
2. Все проверки через GitHub API (жив ли `daaef29e` и 6 старых хешей от
   20 июля, приватность репозитория, число форков, релизы, pull
   request'ы) — упёрлись в отсутствие авторизации (`gh` не установлен,
   токена нет), без входа в браузер под аккаунтом `fortunaeg-le`
   содержательного ответа получить нельзя.
3. Если после push окажется, что GitHub всё ещё кэширует старые коммиты
   (через forks, PR-ссылки, GitHub Actions caches, GitHub CDN/kv-хранилища
   raw-объектов) — это зона ответственности GitHub Support
   (`https://support.github.com/`), запрос на полную очистку кэшей и
   deleted-object storage может подать только владелец аккаунта.
