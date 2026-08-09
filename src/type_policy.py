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
#: ИНН ФИЗИЧЕСКОГО ЛИЦА (`INN_PERSON`, 12 цифр) входит в набор с этапа T2-INN.
#: До него ИНН был ОДНИМ типом на обе длины, и набор был вынужден исключать его
#: целиком: включить значило бы маскировать заодно ИНН организаций, которые ПДн
#: не являются. Ценой была настоящая утечка — ИНН предпринимателя по умолчанию
#: оставался в тексте открытым. Теперь длина вынесена в ТИП (`INN` = 10 цифр,
#: организация; `INN_PERSON` = 12 цифр, человек), и набор делится по смыслу.
#: ЭТАП TYPE-FACTORY (шаг 2): составы наборов читаются из собранного
#: entity_types.yaml (ключ `sets:` у типа — его пишет файл-описание фабрики
#: либо base). Кортежи ниже — УМОЛЧАНИЕ на случай недоступного конфига; для
#: дофабричных типов конфиг и умолчание обязаны совпадать по составу —
#: страж tests/test_type_factory.py::test_type_policy_sets_match_config.
#: ЭТАП MERGE-2 (2026-08-09, санкция владельца): + `PASSPORT_ISSUER` — орган
#: выдачи паспорта. Это ПРЯМЫЕ персональные данные: орган выдачи вместе с
#: датой сужают круг лиц не хуже номера паспорта, а номер уже здесь — набор,
#: в котором номер закрыт, а «кем выдан» открыт, выглядел бы осмысленным и не
#: защищал. Парная строка — `sets: [personal, …]` в
#: tests/corpus_v2/typedefs/PASSPORT_ISSUER.yaml (состав читается ОТТУДА,
#: кортеж ниже — умолчание на случай недоступного конфига).
PERSONAL_DEFAULT = (
    "PERSON", "ADDRESS", "PHONE", "EMAIL", "PASSPORT", "SNILS", "BIRTHDATE",
    "INN_PERSON", "PASSPORT_ISSUER",
)

#: 2. Персональные данные и реквизиты: + ИНН организации, ОГРН, КПП, БИК, счета,
#: организации. `INN` здесь — ровно 10-значный ИНН юрлица (12-значный уже в
#: наборе 1 и приходит сюда вместе со всем набором 1).
PERSONAL_REQUISITES_DEFAULT = PERSONAL_DEFAULT + (
    "INN", "OGRN", "KPP", "BIK", "BANK_ACCOUNT", "ORG",
)

#: 3. Всё, включая деньги: + SUM, PERCENT, TERM.
#:
#: ЭТАП T2: процент и срок входят в ЭТОТ набор, а не в отдельный. Решение
#: владельца назвало три вида данных одним пунктом («деньги, проценты и сроки
#: маскируются»), и в договоре они живут одной фразой: «0,1 % от цены Договора
#: за каждый день просрочки» — это цена вместе с условием. Разведение их по
#: разным наборам означало бы набор, в котором сумма закрыта, а ставка от неё
#: открыта, — то есть настройку, которая выглядит осмысленной и не защищает.
WITH_MONEY_DEFAULT = PERSONAL_REQUISITES_DEFAULT + ("SUM", "PERCENT", "TERM")


def _sets_from_config():
    """(personal, personal_requisites, with_money) из ключей `sets:` типов
    собранного конфига; None — конфиг недоступен или sets нет ни у кого."""
    import os

    try:
        from config_cache import default_config_path, load_yaml_cached
        config = load_yaml_cached(default_config_path())
    except Exception:
        return None
    per, req, mon = [], [], []
    seen_any = False
    for t, spec in config.get("entity_types", {}).items():
        if not isinstance(spec, dict) or "sets" not in spec:
            continue
        seen_any = True
        s = spec["sets"] or []
        if "personal" in s:
            per.append(t)
        if "personal_requisites" in s:
            req.append(t)
        if "with_money" in s:
            mon.append(t)
    if not seen_any:
        return None
    return tuple(per), tuple(req), tuple(mon)


_from_config = _sets_from_config()
if _from_config is not None:
    PERSONAL, PERSONAL_REQUISITES, WITH_MONEY = _from_config
else:
    PERSONAL = PERSONAL_DEFAULT
    PERSONAL_REQUISITES = PERSONAL_REQUISITES_DEFAULT
    WITH_MONEY = WITH_MONEY_DEFAULT

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

#: Одна строка на набор — что он маскирует, ЧЕЛОВЕЧЕСКИМ языком, без имён типов
#: из кода (этап T1-UI, шаг 2: экран читает юрист, а не программист). Строки
#: живут рядом с составом наборов НАМЕРЕННО: разошедшись, состав и его описание
#: обманут пользователя молча — здесь они правятся в одном месте.
PROFILE_HINTS = {
    PROFILE_PERSONAL:
        "ФИО, адреса, телефоны, почта, паспорта, СНИЛС, даты рождения, "
        "ИНН человека (12 цифр)",
    PROFILE_PERSONAL_REQUISITES:
        "всё перечисленное выше и, кроме того, названия организаций, ИНН "
        "организации (10 цифр), ОГРН, КПП, БИК, банковские счета",
    PROFILE_WITH_MONEY:
        "всё перечисленное выше и, кроме того, денежные суммы, процентные "
        "ставки и сроки исполнения",
    PROFILE_MAXIMUM:
        "все типы данных, которые программа умеет находить, без исключений",
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


def save_settings(profile, types=None, path=None) -> Path:
    """Записать файл настроек — ТО ЖЕ место и ТОТ ЖЕ формат, что читает
    `load_settings` (второго хранилища настроек в программе нет и не заводится).

    Запись атомарная (временный файл рядом + `os.replace`): настройку правит
    экран интерфейса, и оборванная на середине запись оставила бы юриста с битым
    JSON, то есть молча сброшенным набором. Неизвестное имя набора не пишется —
    сводится к умолчанию здесь же, чтобы в файле не оседал мусор.
    """
    import os

    if profile not in PROFILES:
        profile = DEFAULT_PROFILE
    types = {t: bool(v) for t, v in dict(types or {}).items() if isinstance(t, str)}

    p = Path(path) if path is not None else settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(
        json.dumps({"profile": profile, "types": types}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, p)
    return p


#: Следы прошлой работы программы в `~/.shifrator` (этап T1-UI, шаг 1). Список
#: ЗАКРЫТЫЙ и намеренно не «в каталоге вообще что-то есть»: рядом лежит
#: `launcher.lock`, который создаёт САМ запуск интерфейса — по нему первая
#: установка выглядела бы обновлением. Здесь только то, что появляется
#: исключительно от РАБОТЫ пользователя: хранилище сессий, ключ шифрования
#: сессий, ручная разметка.
_WORK_TRACES = (
    ("sessions", "dir"),          # storage/session_store: ~/.shifrator/sessions
    ("sessions/key.bin", "file"),  # ключ Fernet — создаётся первой шифрацией
    ("markup", "dir"),            # ручная разметка (U3): ~/.shifrator/markup
)


def has_previous_work(base=None) -> bool:
    """Есть ли на диске следы прошлой работы программы. Каталог-след считается
    следом, только если он НЕПУСТ: пустую папку могла создать неудавшаяся
    попытка, а решение «это обновление» она обосновывать не должна."""
    base = Path(base) if base is not None else settings_path().parent
    for name, kind in _WORK_TRACES:
        p = base / name
        try:
            if kind == "dir":
                if p.is_dir() and any(p.iterdir()):
                    return True
            elif p.is_file():
                return True
        except OSError:
            continue
    return False


def ensure_settings(path=None) -> dict:
    """Однократное решение при ПЕРВОМ запуске версии с настройкой типов
    (этап T1-UI, шаг 1). Возвращает {"created": bool, "profile": str}.

    * файл настроек ЕСТЬ — не трогаем ничего, выбор пользователя уважается;
    * файла нет, но есть следы прошлой работы (`has_previous_work`) — это
      ОБНОВЛЕНИЕ: пишем «Максимум», то есть сохраняем прежнее поведение
      программы (до T1 маскировалось всё). Молча сузить набор у человека,
      который уже работает, — значит изменить результат его работы без спроса;
    * файла нет и следов нет — ПЕРВАЯ УСТАНОВКА: пишем умолчание
      (`DEFAULT_PROFILE` = только персональные данные).

    Решение принимается один раз: дальше файл существует, и ветка «файл есть»
    закрывает вопрос навсегда. Ошибка записи (нет прав, только чтение) не
    считается фатальной — программа продолжит работать на умолчаниях
    `load_settings`, а не откажется обезличивать документ.
    """
    p = Path(path) if path is not None else settings_path()
    if p.exists():
        return {"created": False, "profile": load_settings(p)["profile"]}

    profile = PROFILE_MAXIMUM if has_previous_work(p.parent) else DEFAULT_PROFILE
    try:
        save_settings(profile, {}, path=p)
    except OSError:
        return {"created": False, "profile": profile}
    return {"created": True, "profile": profile}


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
