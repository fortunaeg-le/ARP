# История нейросетевых компонентов SHIFRATOR

Только чтение репозитория (git log, `docs/archive/`, `experiments/`, `FINDINGS.md`,
`STATE.md`), полный корпус не гонялся. У каждого числа — источник (файл:строка).
Где число в репозитории не сохранилось — так и написано, по памяти не восстановлено.

---

## 1. Natasha — замеры чистого NER на корпусе

### 1.1 Что именно замерялось

Natasha (её нейросетевые теггеры `NewsNERTagger`/`NewsEmbedding` + грамматический
`AddrExtractor`/`yargy`) была изначальным движком ORG/PER/ADDRESS. Три этапа (A, B, C)
последовательно заменили её NER-слой для этих трёх типов на структурный
(«якорь + реестр + проход 2», `src/anchor_registry.py`), но каждый этап начинается с
замера ИМЕННО чистой Natasha «до» — этот замер и есть точка отсчёта.

### 1.2 ORG — этап A (2026-07-20)

Источник: [`docs/archive/reports/HANDOFF_STAGE_A_ORG.md:42-54`](archive/reports/HANDOFF_STAGE_A_ORG.md).

Фронт-1, `dogovor.docx`, дамп Natasha «до» взят из
`experiments/a0_gliner/dumps/natasha_dogovor.json` (178 ORG-спанов):

| категория мусора (Natasha → ORG) | Natasha | структурный движок |
|---|---:|---:|
| Большинством/Большинство | 24 | 0 |
| Долю/Долям/Доля | 24 | 0 |
| ПОДПИСИ СТОРОН | 1 | 0 |
| Общество (алиас) | 39 | 0 |
| Участник/Участники/Участников | 16 | 0 |
| заголовки разделов | 48 | 0 |
| **итого мусора** | **~152** | **0** |

«Восход» (122 падежных вхождения реальной организации): Natasha — **3 типа**
(PERSON 48, ADDRESS 48, ORG 2); структурный движок — **единый ORG, 122/122**
([`HANDOFF_STAGE_A_ORG.md:56-58`](archive/reports/HANDOFF_STAGE_A_ORG.md)).

Фронт-2, полный корпус (324 док.), таблица `baseline → этап A`
([`HANDOFF_STAGE_A_ORG.md:70-80`](archive/reports/HANDOFF_STAGE_A_ORG.md)):

| метрика | baseline (Natasha-ORG) | этап A (структурный) |
|---|---:|---:|
| ORG recall (found/462) | 408 (88.3%) | 415 (89.8%) |
| ORG маски всего | 4002 | 1011 |
| ORG FP на негативах | 682 | 1 |
| masking B (границы) | 79.59% | 81.80% |

Позже (STATE.md:273) зафиксировано дальнейшее ORG recall 88.3→93.9% после этапа A′ —
это уже правки структурного движка, не повторный замер Natasha.

### 1.3 PER — этап B (2026-07-22)

Источник: [`docs/archive/reports/HANDOFF_STAGE_B_PER.md:78-95`](archive/reports/HANDOFF_STAGE_B_PER.md),
изолированная точка отсчёта — HEAD этапа A′ (Natasha-PER ещё активна), сравнение с
`PerAnchorDetector`:

| тип | n | recall (Natasha→структ.) | exact среди found | leak_v2≥6 |
|---|--:|---|---|---|
| PER | 3420 | 72.2% → 71.7% | 77.8% → 87.6% | 41.3% → 28.7% |
| ORG (контроль) | 462 | 93.9% → 94.2% | 97.5% → 97.5% | 19.0% → 20.6% |
| ADDRESS (контроль) | 1308 | 93.7% → 93.7% | 42.2% → 41.4% | 13.1% → 12.8% |

Ложных PER на негативах: **11 → 1** ([`HANDOFF_STAGE_B_PER.md:108`](archive/reports/HANDOFF_STAGE_B_PER.md)).
masking B (границы, косвенный падеж одним спаном): 81.87% → 89.75%
([`HANDOFF_STAGE_B_PER.md:94`](archive/reports/HANDOFF_STAGE_B_PER.md)).

### 1.4 ADDRESS — этап C (2026-07-22/23)

Источник: [`docs/archive/reports/HANDOFF_STAGE_C_ADDRESS.md:69-77`](archive/reports/HANDOFF_STAGE_C_ADDRESS.md),
точка отсчёта — HEAD `stage-b-per` (Natasha `AddrExtractor`/LOC ещё определяют границы
жадно), сравнение с якорным гейтом:

| метрика ADDRESS | HEAD (Natasha, жадный) | этап C (якорный) |
|---|---:|---:|
| recall | 93.7% | 87.5% |
| exact-границы (среди found) | 41.4% | 65.1% |
| ложных ADDRESS на негативах | 634 | 1 |
| ложных ADDRESS всего | 3483 | 14 |
| FP всего по всем типам | 1283 | 650 |

После точечного фикса сеяния (`_addr_has_seed`, 2026-07-23) recall поднят
87.5% → 89.0% ([`HANDOFF_STAGE_C_ADDRESS.md:286`](archive/reports/HANDOFF_STAGE_C_ADDRESS.md)).
Соседние типы (контроль на том же прогоне): ORG recall 94.2%=94.2%, PER recall
71.7%→74.1% ([`HANDOFF_STAGE_C_ADDRESS.md:79-80`](archive/reports/HANDOFF_STAGE_C_ADDRESS.md)) —
это и есть число «PER recall 74%», на которое ссылается текущий STATE.

Во всех трёх этапах красная линия гейта по `leak_v2` документирована как
**MASK-SHIFT** (выключение Natasha сняло случайную маскировку, которую создавали
её собственные мислейблы, и обнажило пред-существующие дыры PER/ADDRESS/числовых
детекторов) — не регрессия детекции, см. `HANDOFF_STAGE_A_ORG.md` §3,
`HANDOFF_STAGE_B_PER.md` §«ПРИЁМКА — Фронт-2», `HANDOFF_STAGE_C_ADDRESS.md` §5/§9.

### 1.5 Что от Natasha осталось в продукте сегодня (по коду)

Проверено `grep` по `src/` (не по памяти):

- **`src/ner_detector.py:64-78`** — импортирует `MorphVocab`, `NewsEmbedding`,
  `NewsNERTagger`, `AddrExtractor` из `natasha`. `NewsNERTagger` даёт LOC-спаны,
  которые по-прежнему используются как один из двух источников (LOC ∪ yargy) для
  сборки адресных кандидатов ДО якорного гейта (`entity_types.yaml:32-33`:
  `method: ner`, `ner_extractor: addr` — «НЕ ner_label/LOC… используется
  natasha.AddrExtractor, а не NER-тэггер», но LOC-хиты остаются триггером сеяния
  yargy, см. `ner_detector.py:106-158`). `natasha.AddrExtractor` (yargy-парсер) —
  по-прежнему единственный грамматический источник адресных кандидатов.
- **`src/anchor_registry.py:486`** — использует `natasha.MorphVocab.inflect` для
  склонения канонической формы ФИО/ORG при восстановлении.
- **`src/syntax_compound.py:43-73`** — импортирует `NewsMorphTagger` (свой
  `NewsEmbedding`) для синтаксического разбора (appos-склейка составных сущностей
  «ИП + ФИО»).
- **`src/tokenizer.py:515-582`** — импортирует `natasha.Doc` для сегментации текста
  в B3-граничном проходе.
- **`entity_types.yaml:18,28`** — ORG и PERSON явно переключены на
  `method: anchor_registry`; `ner_label` для них убран, поэтому
  `_load_ner_config` НЕ кладёт их в `ner_label_map` — `NewsNERTagger` для ORG/PER
  фактически не вызывается (комментарии `entity_types.yaml:10,21-22`).

Итог: Natasha как NER-классификатор ORG/PERSON выключена полностью; как источник
сегментации (`Doc`), морфологии (`MorphVocab`), синтаксиса (`NewsMorphTagger`+
NewsSyntaxParser, см. `HANDOFF_STAGE_B_PER.md`) и адресного парсера
(`AddrExtractor`/yargy + LOC-триггер) — остаётся инфраструктурным ядром пайплайна.

---

## 2. GLiNER — этап A0 (bake-off, 2026-07-20)

Источник: [`docs/archive/reports/HANDOFF_A0_GLINER.md`](archive/reports/HANDOFF_A0_GLINER.md)
и таблицы [`experiments/a0_gliner/tables/summary.md`](../experiments/a0_gliner/tables/summary.md).
Коммит замера — `30b6d8a experiment(a0): bake-off GLiNER vs Natasha — замер, вердикт НЕТ`
(`git log`).

### 2.1 Что лежит в `experiments/a0_gliner/`

Скрипты: `00_smoke.py`…`07_emit_tables.py` (8 шт., подготовка входа → прогон GLiNER →
анализ → таблицы). Входы: `input/normalized_segments.json`,
`input/synthetic_segments.json`. Дампы сырья: `dumps/gliner_*_thNN.json` (по
5 порогов × 2 модели × 2 набора labels), `dumps/natasha_dogovor.json`,
`dumps/natasha_synthetic.json`. Таблицы: `tables/results.json`,
`tables/results_synthetic.json`, `tables/rolewords_grid.json`, `tables/summary.md`.
Лог установки: `install_log.txt`. Изолированный venv `venv_gliner/` и кэш моделей
`hf_cache/` (3.3 ГБ) — gitignored, в коммит не входят.

### 2.2 Модели

- `urchade/gliner_multi-v2.1` — apache-2.0, backbone мультиязычный, датасет
  `urchade/pile-mistral-v0.1` ([`HANDOFF_A0_GLINER.md:30-38`](archive/reports/HANDOFF_A0_GLINER.md)).
- `urchade/gliner_multi_pii-v1` — apache-2.0, fine-tune multi-v2.1 на
  `urchade/synthetic-pii-ner-mistral-v1` (синтетический, англоцентричный PII-датасет).

### 2.3 Числа

**dogovor.docx, зеркало «Восход»** (122 вхождения реальной организации), лучшая
рабочая точка th=0.3 ([`HANDOFF_A0_GLINER.md:70`](archive/reports/HANDOFF_A0_GLINER.md),
таблица в `tables/summary.md:19-27`):

| | Natasha | GLiNER multi-v2.1 ru | GLiNER pii-v1 ru |
|---|---:|---:|---:|
| recall | 80% (98/122) | **100%** | **100%** |
| типов | 3 (PER/ADDR/ORG) | **1 (ORG)** | **1 (ORG)** |

**Синтетическая подвыборка** (22 док., НЕ полный корпус), покрытие сущностей
Natasha, приёмка — пропуск ≤10% ([`HANDOFF_A0_GLINER.md:123-131`](archive/reports/HANDOFF_A0_GLINER.md),
таблица `tables/summary.md:56-63`):

| порог | multi-v2.1 покрытие | пропуск |
|---|---|---|
| 0.1 (лучшая точка) | 81% (687/847) | **19%** |
| 0.3 | 70% | 30% |
| 0.5 | 61% | 39% |

Критерий ≤10% не выполнен ни на одном пороге; даже по «своему» типу PERSON
same-type-покрытие 258/292 = 88% (пропуск 12%,
[`HANDOFF_A0_GLINER.md:130-131`](archive/reports/HANDOFF_A0_GLINER.md)).

**(c) over-masking родовых слов** (участник/общество/…), критерий «не хуже
Natasha=150» ([`HANDOFF_A0_GLINER.md:93-98`](archive/reports/HANDOFF_A0_GLINER.md)):

| порог | Natasha | multi-v2.1 ru | pii-v1 ru |
|---|---:|---:|---:|
| 0.1 | 150 | 360 | 346 |
| 0.3 | 150 | 308 | 319 |
| 0.7 | 150 | 134 | 172 |

Проходит только при th≥0.7 у multi-v2.1 — но там recall «Восход» падает до 41-30%.

**Латентность (CPU)** ([`HANDOFF_A0_GLINER.md:155-165`](archive/reports/HANDOFF_A0_GLINER.md)):
Natasha — 2.9 c/dogovor, экстраполяция на 324 док. ~13 мин; GLiNER multi-v2.1 —
60.5 c/dogovor, экстраполяция ~47 мин (~2-3× медленнее).

### 2.4 Причина отклонения (сформулирована явно, не восстановлена по памяти)

[`HANDOFF_A0_GLINER.md:167-184`](archive/reports/HANDOFF_A0_GLINER.md), сводная
таблица критериев приёмки — 5 критериев, ни одна точка сетки порогов не сходится
по всем пяти одновременно. Опорный (единственный красный на ВСЕХ порогах) —
критерий №3, синтетический пропуск ≥19% (минимум), при доказанно равном входе
(§5 отчёта: оба движка получают один и тот же `detection_view()`-текст,
проверено побайтно на конкретном примере `loan_0002/p5`). Вердикт дословно:
«GLiNER не может быть генератором кандидатов ВМЕСТО Natasha: он теряет ~1/5–1/3
чистых реквизитов, которые Natasha берёт надёжно… и приносит свой массовый
over-masking родовых слов». Итоговое решение сессии — GLiNER откладывается «в
ярус recall» (нереализованный, будущий), этап A строится structure-first (без NN).

---

## 3. Другие кандидаты

Поиск по `docs/` (все файлы, включая `archive/`) на `DeepPavlov|spaCy|RuBERT|
BERT-NER|Slovnet|Stanza|Flair` (регистронезависимо) дал совпадения только в
`LICENSES_AUDIT.md`, `THIRD_PARTY_LICENSES.md`, `archive/handoffs/HANDOFF_3.md`,
`archive/SHIFRATOR_SPEC_AI.md` — во всех случаях это упоминания `slovnet`/`navec`
как **внутренних зависимостей самой Natasha** (её NER-веса), не как отдельно
рассматриваемого кандидата-конкурента.

Поиск по `git log --all` (сообщения коммитов) на «модел|нейросет|natasha|gliner»
не дал коммитов, упоминающих иные модели, кроме Natasha и GLiNER.

**Вывод: следов замера или обсуждения DeepPavlov, spaCy ru, RuBERT-NER или любой
другой третьей NER-модели в репозитории нет.** Единственный формально
рассмотренный кандидат на замену Natasha — GLiNER (см. §2).

---

## 4. Текущие дыры, где нейросеть могла бы помочь

### 4.1 PER recall 74% — безъякорные люди, нижний регистр

Число подтверждено: [`docs/archive/reports/HANDOFF_STAGE_C_ADDRESS.md:79-80`](archive/reports/HANDOFF_STAGE_C_ADDRESS.md)
(«PER recall 72.0→74.1%»), также [`docs/STATE.md:317`](STATE.md).

Природа дыры (`docs/FINDINGS.md:37`, находка **Aprime-1**): голая безъякорная
фамилия («…и Иванов подписал…» без реквизитов, без должности, без
«гражданин/гражданка», без соседнего ИНН-12/СНИЛС) **сознательно не якорится**
структурным `PerAnchorDetector` — движок явно требует внешний маркер, иначе
«сомнение = не метить» (`docs/archive/reports/HANDOFF_STAGE_B_PER.md:20-22`).
Это основная причина недобора recall PER относительно этапа A′
(`HANDOFF_STAGE_B_PER.md:119`).

**Закрывается ли правилами?** Отчасти нет — сама природа дефекта в том, что
у безъякорной фамилии в прозе **нет структурного признака**, отличающего её от
любого другого капитализированного/строчного слова: правило потребовало бы
внешнего словаря фамилий (осознанно отвергнуто на этапе B — риск роста FP на
нарицательных нового языка, `docs/archive/reports/DECISIONS.md`, ORG-аналог **Aprime-1**
отклонён этапом A как «слишком широкий», `FINDINGS.md:37`). Это ровно класс, где
нужна семантика/статистика контекста (кто из капитализированных/строчных слов —
имя человека), а не детерминированная грамматическая помета — то есть кандидат
на нейросетевой генератор recall-яруса, который отчёт A0 отложил, а не отверг
целиком (§2.4: «GLiNER → ярус recall позже»).

### 4.2 Дата рождения с омоглифами

Находка `ADDRB-BIRTHDATE-EXPOSED` (`docs/FINDINGS.md:17`): дата вида
«21.0І.196О» (кириллические `І`/`О` вместо `1`/`0`) детектором BIRTHDATE не
берётся никогда — нормализатор омоглифов НАМЕРЕННО не сводит одиночную букву
внутри числа (защита корректного «кв. 2б»,
`docs/FINDINGS.md:17` / `Stage3-A`, `docs/FINDINGS.md:15-16`).

**Закрывается ли правилами?** Да, в принципе — это чисто символьная задача
(различить «буква как часть адресного/номерного текста» и «буква-гомоглиф цифры
внутри известного формата даты»); ограничение сегодня — политика нормализатора
(порог «≥2 настоящих цифры» для свода), а не отсутствие семантики. Формат даты
(`DD.MM.YYYY`) сам по себе достаточный контекст для точечного правила без ML.

### 4.3 Разорванный ИНН

Находка `T2-A` (`docs/archive/reports/T2_INN_REPORT.md:251-273`): 12-значный ИНН физлица,
разорванный переносом строки или границей ячейки таблицы, детектируется только
10-значным префиксом — механизм сборки разорванных значений
(`src/multispan.py`, этап E′) на ИНН не распространён (класс `SplitEntities`,
пред-существующий). 16 масок в 14 документах, все на адверсариальных мутациях
(13 `linebreak`, 1 `digit_spaces`); на чистых документах не встречается
(`docs/archive/reports/T2_INN_REPORT.md:261-262`). Следствие для набора «только персональные
данные» (тип `INN` организации выключен): такой обрывок остаётся открытым
(`docs/archive/reports/T2_INN_REPORT.md:267-273`).

**Закрывается ли правилами?** Да — это тот же класс, что уже решён для ADDRESS
этапом E′ (`_addr_deweave`, «расплетающий» вид, см. `FINDINGS.md:22`) и для PHONE
(`src/multispan.py`). Распространение существующего мультиспан-механизма на ИНН —
чисто структурная доработка, семантика не нужна.

### 4.4 Общий вывод по разделу

Из трёх факт-дыр только PER-recall на безъякорных именах — класс, где текущая
архитектура сознательно остановилась перед задачей, требующей семантики/
статистики контекста, а не детерминированного правила (это и есть предмет
отложенного «яруса recall» из вердикта A0, §2.4). BIRTHDATE-гомоглифы и
разорванный ИНН — доработки существующих детерминированных механизмов
(нормализатор, мультиспан), уже применённых к другим типам сегодня.
