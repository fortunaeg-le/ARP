"""ЭТАП T1 — переключатель типов данных: КАКИЕ типы маскировать.

ГЛАВНОЕ (контракт этапа). Выключенный тип НЕ отключается в ДЕТЕКЦИИ. Детекция
работает целиком, всегда, всеми слоями; фильтр стоит на этапе МАСКИРОВАНИЯ —
сущность найдена, участвует в разрешении пересечений (`tokenizer._resolve_overlaps`),
служит барьером соседям, и только ПОСЛЕ этого не получает токен.

Причина не в экономии кода, а в границах масок. Разрешение пересечений —
взаимное: адрес не даёт телефону внутри себя разрастись, regex-реквизит
обрезает адрес до непересекающейся части (`tokenizer._trim_to_free`), ORG/PER
подаются барьерами в `ner_detector._address_barriers`. Убери тип из детекции —
исчезнет барьер, и границы масок СОСЕДНИХ, включённых типов поедут. То есть
выключение «Телефона» изменило бы вид маски «Адреса». Это не оптимизация, это
дефект. Проверка утверждения — `tests/test_type_policy.py`
(`test_disabling_in_detection_would_move_neighbour_boundaries`).

Что здесь есть:
  * четыре НАБОРА (профиля) над типами, которые программа знает СЕГОДНЯ;
  * пользовательские перекрытия «включить/выключить отдельный тип» поверх набора;
  * чтение файла настроек `~/.shifrator/settings.json` (неизвестный тип и
    неизвестный набор ИГНОРИРУЮТСЯ, программа не падает — файл настроек пишет
    человек, а отказ работать из-за опечатки в нём хуже, чем работа по умолчанию).

Чего здесь НЕТ: замер и гейт этот модуль не зовут вовсе — они обязаны мерить
работу программы, а не настройку пользователя, и потому вызывают токенизацию с
`enabled_types=None` (набор «Максимум», фильтр не применяется). См. §ШАГ 3
задания этапа и `tests/corpus/run_measurement.py`.
"""

import json
from pathlib import Path

# --------------------------------------------------------------------------- #
# Наборы. Ключи — ключи `entity_types` из entity_types.yaml (они же
# `Entity.entity_type`), не сокращения замера (`PER`/`ACCOUNT` — это словарь
# `measure_lib.ALL_ENTITY_TYPES`, другой слой).
# --------------------------------------------------------------------------- #

#: 1. Только персональные данные — НАБОР ПО УМОЛЧАНИЮ (юристу нужен он).
#:
#: ИНН в набор НЕ входит, и это вынужденно. ИНН физического лица (12 цифр) — это
#: ПДн, ИНН организации (10 цифр) — нет, но программа НЕ различает их как разные
#: типы: в entity_types.yaml один тип `INN` с одним паттерном на 10 и 12 цифр, и
#: `regex_detector` эмитит `entity_type="INN"` в обоих случаях. Число цифр
#: используется лишь как ВНУТРЕННИЙ признак в `anchor_registry` (ИНН-12 → якорь
#: PER), наружу, в тип сущности, оно не выходит. Разделить набор по «12 цифр» тут
#: нельзя, не заведя 14-й… то есть 15-й тип — а этап новых типов не вводит.
#: Поэтому ИНН целиком отнесён к набору 2 (см. отчёт этапа T1).
PERSONAL = (
    "PERSON", "ADDRESS", "PHONE", "EMAIL", "PASSPORT", "SNILS", "BIRTHDATE",
)

#: 2. Персональные данные и реквизиты: + ИНН, ОГРН, КПП, БИК, счета, организации.
PERSONAL_REQUISITES = PERSONAL + (
    "INN", "OGRN", "KPP", "BIK", "BANK_ACCOUNT", "ORG",
)

#: 3. Всё, включая деньги: + SUM.
WITH_MONEY = PERSONAL_REQUISITES + ("SUM",)

#: 4. Максимум — ВСЕ типы, которые знает конфиг. Задан не списком, а сентинелом
#: `None`: «максимум» обязан оставаться максимумом и после появления 15-го типа,
#: а список пришлось бы синхронизировать руками (ровно тот класс рассинхрона,
#: против которого заведён `src/pipeline.py`). Этот же набор — единственный, на
#: котором считаются замер и гейт.
MAXIMUM = None

PROFILE_PERSONAL = "personal"
PROFILE_PERSONAL_REQUISITES = "personal_requisites"
PROFILE_WITH_MONEY = "with_money"
PROFILE_MAXIMUM = "maximum"

#: Порядок — порядок показа пользователю (от узкого к широкому).
PROFILES = (
    PROFILE_PERSONAL, PROFILE_PERSONAL_REQUISITES, PROFILE_WITH_MONEY, PROFILE_MAXIMUM,
)

_PROFILE_TYPES = {
    PROFILE_PERSONAL: PERSONAL,
    PROFILE_PERSONAL_REQUISITES: PERSONAL_REQUISITES,
    PROFILE_WITH_MONEY: WITH_MONEY,
    PROFILE_MAXIMUM: MAXIMUM,
}

PROFILE_LABELS = {
    PROFILE_PERSONAL: "Только персональные данные",
    PROFILE_PERSONAL_REQUISITES: "Персональные данные и реквизиты",
    PROFILE_WITH_MONEY: "Всё, включая деньги",
    PROFILE_MAXIMUM: "Максимум (все типы)",
}

DEFAULT_PROFILE = PROFILE_PERSONAL

SETTINGS_FILENAME = "settings.json"


def known_types(config_path: str) -> tuple[str, ...]:
    """Типы, которые программа умеет МАСКИРОВАТЬ = записи entity_types.yaml с
    `token_prefix` (без префикса тип нечем заменить в тексте). Порядок — как в
    файле. `enabled: false` здесь НЕ фильтруется: это выключатель ДЕТЕКТОРА
    (сегодня — только `DATE`), другой уровень, и знать о типе политика обязана
    даже когда детектор молчит."""
    import yaml

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return tuple(
        t for t, spec in config["entity_types"].items()
        if isinstance(spec, dict) and "token_prefix" in spec
    )


def resolve(profile=DEFAULT_PROFILE, overrides=None, known=None) -> frozenset[str] | None:
    """Состав набора: имя профиля + пользовательские перекрытия -> множество
    ВКЛЮЧЁННЫХ типов (или None = «Максимум», фильтр не применяется вовсе).

    `overrides` — {тип: True|False} поверх набора. Неизвестный профиль молча
    сводится к умолчанию, неизвестный тип в перекрытиях молча игнорируется, если
    известный состав `known` передан; без `known` фильтровать перекрытия не по
    чему, и они берутся как есть (тип, которого нет, всё равно ни на что не
    ляжет).

    None возвращается ТОЛЬКО когда профиль «Максимум» и ни одно перекрытие
    ничего не выключает — иначе состав считается явным множеством.
    """
    base = _PROFILE_TYPES.get(profile, _PROFILE_TYPES[DEFAULT_PROFILE])
    overrides = dict(overrides or {})
    if known is not None:
        overrides = {t: v for t, v in overrides.items() if t in known}

    if base is MAXIMUM:
        if not any(v is False for v in overrides.values()):
            return MAXIMUM
        if known is None:
            raise ValueError(
                "выключение отдельного типа поверх набора «Максимум» требует "
                "известного состава типов (known=...)"
            )
        enabled = set(known)
    else:
        enabled = set(base)

    for t, on in overrides.items():
        if on:
            enabled.add(t)
        else:
            enabled.discard(t)
    return frozenset(enabled)


def settings_path() -> Path:
    """`~/.shifrator/settings.json`. Считается при каждом вызове (а не на импорте),
    чтобы тесты могли подменить домашнюю директорию."""
    return Path.home() / ".shifrator" / SETTINGS_FILENAME


def load_settings(path=None) -> dict:
    """Читает файл настроек. НИКОГДА не бросает из-за содержимого файла: нет
    файла / битый JSON / чужая структура — возвращаются умолчания. Отказ
    анонимизировать документ из-за опечатки в необязательном файле настроек —
    хуже, чем работа по умолчанию (при этом умолчание — САМЫЙ УЗКИЙ набор, то
    есть ошибка не приводит к неожиданному раскрытию: она приводит к тому, что
    замаскировано будет не меньше, чем обещает умолчание).

    Возвращает {"profile": str, "types": {тип: bool}} — сырое, без сверки с
    составом конфига (её делает `resolve(..., known=...)`).
    """
    p = Path(path) if path is not None else settings_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"profile": DEFAULT_PROFILE, "types": {}}
    if not isinstance(raw, dict):
        return {"profile": DEFAULT_PROFILE, "types": {}}

    profile = raw.get("profile")
    if profile not in PROFILES:
        profile = DEFAULT_PROFILE

    types = raw.get("types")
    if not isinstance(types, dict):
        types = {}
    types = {t: bool(v) for t, v in types.items() if isinstance(t, str)}
    return {"profile": profile, "types": types}


def enabled_types(config_path: str, settings=None, path=None) -> frozenset[str] | None:
    """Готовый ответ для `tokenizer.tokenize(..., enabled_types=...)`:
    файл настроек + состав конфига -> множество включённых типов (или None)."""
    s = settings if settings is not None else load_settings(path)
    return resolve(s["profile"], s["types"], known=known_types(config_path))


def describe(config_path: str, enabled) -> dict:
    """Состав включённых типов для ОТЧЁТА программы (шаг 4 этапа T1): без него
    цифры отчёта двусмысленны — непонятно, тип не нашёлся или был выключен.
    Только структурные коды типов, никакого текста документа."""
    known = known_types(config_path)
    on = set(known) if enabled is None else set(enabled)
    return {
        "enabled_types": [t for t in known if t in on],
        "disabled_types": [t for t in known if t not in on],
    }
