# WERT_CLEANUP — удаление wert.docx из истории git

Дата операции: 2026-07-28.
Контекст: docs/REAL_DOCS_CHECK.md зафиксировал, что в истории репозитория
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
`tests/corpus/subsample.py` и новые файлы `docs/ENTITY_SPEC.md`,
`docs/GENV_REPORT.md`, `docs/HANDOFF_SUBSET_ITER.md`,
`docs/MARKUP_RULES.md`, `docs/PERF_REPORT.md`, `docs/T0V_REPORT.md`.

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
c7b13b0 DOC-LEGAL-CHECK: фактическая проверка PRODUCT_LEGAL.md (docs/LEGAL_CHECK.md)
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

## Итог

- `wert.docx` физически удалён из истории и из object store (проверено
  точечно по хешу блоба).
- Прошлая чистка (`real_docs/`, `dogovor.docx`) осталась нетронутой и
  подтверждена повторно как выполненная.
- Корпус тестов цел (656/656 по MANIFEST).
- Рабочее дерево не пострадало.
- Оба рабочих стеша сохранены.
- `.gitignore` не изменялся.
- Локальный `origin` удалён (побочный эффект filter-repo, не push);
  синхронизация с GitHub не производилась.
