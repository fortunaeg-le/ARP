"""ЕДИНАЯ точка входа конвейера ДЕТЕКЦИИ (этап B, консолидация).

Причина существования: порядок шагов детекции (regex → структурный ORG+PER →
адрес-NER с барьерами → составные сущности) раньше был ПРОДУБЛИРОВАН руками в трёх
местах — shifrator.py (CLI), app/core.py (UI), tests/corpus/run_measurement.py
(замер). Любая правка порядка (этап A′ добавил detect_org ДО detect_ner) требовала
синхронной правки всех трёх; app/core.py однажды отстал на целый этап — UI не давал
ORG-меток, хотя CLI давал (трёхчасовой инцидент, см. archive/reports/HANDOFF_UI.md).

Теперь порядок живёт ЗДЕСЬ, в одной функции run_detection(doc, config_path), и все
три потребителя зовут её. Разъехаться структурно больше нельзя: копии порядка нет.

Функция возвращает СПИСОК Entity, готовый к tokenize (реквизиты + адрес + структурные
ORG/PER + составные). Токенизацию/сохранение сессии каждый потребитель делает сам
(им нужны разные побочные данные — CLI печатает session_id, UI строит экран проверки,
замер меряет round-trip), но ДЕТЕКЦИЯ у всех одна и та же, побайтно.
"""

from anchor_registry import detect_negative_classes, detect_structural, suppress_conflicts
from multispan import collect_multispan
from ner_detector import detect_ner
from regex_detector import detect_regex
from syntax_compound import merge_compound_entities


def run_detection(doc, config_path, skip_address=False):
    """Полный конвейер ДЕТЕКЦИИ для одного SourceDocument. Порядок (единственная копия):

      1. detect_regex — реквизиты (ИНН/ОГРН/счёт/телефон/…) И, с этапа T4, CLAUSE_REF
         (регекс-форма, БЕЗ token_prefix — см. п.6). Считаются ПЕРВЫМИ: дают «карту
         занятой территории» барьером для расширения адреса (W2-D1) и питают
         структурный движок реквизитами юрлица/физлица (ИНН-10/13 → ORG, ИНН-12/СНИЛС
         → PER).
      2. detect_structural — ORG + PER одним движком-реестром (этап A + B): якорь →
         арбитраж типов по лемме ядра → проход 2 по падежам. Natasha-ORG и Natasha-PER
         в конфиге выключены; и ORG, и PER — только структурные.
      3. detect_ner — ТОЛЬКО адрес (natasha.AddrExtractor). Структурные ORG- и
         PER-спаны передаются барьером: адрес не заходит на готовые ORG/ФИО.
      4. suppress_conflicts — снимает адрес-NER, целиком накрытый ядром ORG (тот же
         «Восход», что Natasha когда-то рвала на типы). PER/ORG между собой уже
         разведены реестром движка, поэтому арбитраж здесь только про адрес.
      5. merge_compound_entities — составные «ИП + ФИО» → единый ORG (A-2): appos-склейка
         в сочинительной конструкции теперь корректна, т.к. PER-арбитраж развёл стороны.
      6. detect_negative_classes (ЭТАП T4) — ROLE_TERM/COLLECTIVE, БЕЗ token_prefix.
         Формализация механизмов C′ (M2/M1b), считается ПОСЛЕДНЕЙ и результат
         добавляется к уже собранному списку НЕ через merge_compound_entities —
         отрицательные классы не участвуют в appos-склейке, только в разрешении
         пересечений блока 4 (tokenizer._resolve_overlaps), куда идут барьером.
    """
    regex_e = detect_regex(doc, config_path)
    # ЭТАП E (1b): мультиспан-сборщик — PHONE в произвольной группировке,
    # \n-рваные реквизиты/BIRTHDATE. Часть regex-слоя: его сущности точно так же
    # служат барьерами адресу и участвуют в разрешении пересечений блока 4
    # (дубль со штатным матчем схлопывается там же — длиннейший hull побеждает).
    #
    # ЭТАП A6: структурный движок получает ТОЛЬКО штатные regex-реквизиты, БЕЗ
    # мультиспанов. Рваный вёрсткой реквизит — валидная маска, но НЕ
    # доказательство имени рядом: поисковый вид ORG вырезает '\n', и name-run
    # слева от рваного «ИНН» дотягивается через бывшую границу строки до чужих
    # слов (измерено: ORG «Профсоюзная» на адресе labor_0006__m2718_linebreak;
    # тот же якорь на чистом документе существует, но его случайно давит
    # ADDRESS-маска). До A6 мультиспаны в движок и не попадали: ACCOUNT/PHONE/
    # BIRTHDATE не входят в фильтр _requisites_src_by_seg — строка ниже делает
    # этот инвариант явным, а не побочным.
    ms_e = collect_multispan(doc, config_path)
    struct_e = detect_structural(doc, regex_entities=regex_e)
    regex_e = regex_e + ms_e
    org_e = [e for e in struct_e if e.entity_type == "ORG"]
    per_e = [e for e in struct_e if e.entity_type == "PERSON"]

    # skip_address (ЭТАП DEBT-1) — только для ремонтного прохода layout_repair:
    # он адресные сущности отбрасывает, а считает их дороже всего остального
    # конвейера вместе взятого. Обоснование эквивалентности — docstring detect_ner.
    ner_e = detect_ner(
        doc, config_path,
        regex_entities=regex_e, org_entities=org_e, per_entities=per_e,
        skip_address=skip_address,
    )
    ner_e = suppress_conflicts(struct_e, ner_e)

    entities = regex_e + ner_e + struct_e
    entities = merge_compound_entities(doc, entities)
    # 7. ЭТАП LAYOUT — ремонтный проход: значение, разорванное вёрсткой ВНУТРИ
    #    сегмента (перенос/NBSP внутри токена). Монотонное ДОПОЛНЕНИЕ: основной
    #    вид не меняется, проход может только добавить сущности (страж —
    #    tests/test_layout_repair.py). Внутри ремонтный вид гоняет этот же
    #    run_detection по временному документу (защита от рекурсии — метка
    #    сегмента), поэтому порядок слоёв по-прежнему живёт в одном месте.
    from layout_repair import repair_pass
    entities = entities + repair_pass(doc, config_path, entities)
    entities = entities + detect_negative_classes(doc, config_path)
    return entities
