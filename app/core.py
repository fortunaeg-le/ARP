"""Тонкая обёртка над РЕАЛЬНЫМ конвейером SHIFRATOR для десктоп-интерфейса.

Здесь НЕТ детекции, маскировки или хранения — всё это импортируется из `src/` и
вызывается ровно в том же порядке, что и в `shifrator.py::cmd_encrypt`. Разница
только в том, что CLI печатает в stdout один `session_id`, а интерфейсу нужны ещё
и промежуточные значения (исходный текст, обезличенный текст, список замен,
непрочитанные зоны) — чтобы построить ЭКРАН ПРОВЕРКИ. Мы их просто возвращаем, а
не выбрасываем.

Провенанс порядка вызовов — `shifrator.py::cmd_encrypt` (regex ПЕРВЫМИ как «карта
занятой территории» для detect_ner, затем составные сущности, затем токенизация).
Хранилище сессий — то же, что у CLI (`session_store.default_storage_dir()`), decrypt
находит сессии, созданные здесь. Ничего не уходит в сеть.

--- ЭТАП U3 (экран проверки и разметка) --------------------------------------

Логика детекции НЕ трогается. Добавлено:

  1. Экран проверки теперь всегда рендерится ИЗ СЕССИИ (`_render_state`), а не
     из промежуточного списка `final_entities` — единый путь для «сразу после
     шифрации» и «после ручной правки разметки», иначе эти два рендера рискуют
     разойтись (тот же класс инцидента, что чинил этап B консолидацией
     `pipeline.run_detection` — см. HANDOFF_UI.md).
  2. `{session_id}.doc.json` (`storage.save_doc_segments`/`load_doc_segments`) —
     сегменты исходного документа, нужны, чтобы пересобрать анонимизированный
     текст после ручной правки БЕЗ повторного обращения к исходному файлу (тот
     уже удалён после ответа сервера). Содержит ПОЛНЫЙ исходный текст — ПДн,
     хранится в профиле пользователя рядом с сессией, никуда не отправляется.
  3. Разметка (`{session_id}.markup.json` через `storage.save_markup`/
     `list_markup`/`update_markup`/`delete_markup`) — запись КАЖДОГО действия
     пользователя на экране проверки (пропущено/ложная маска/граница/тип), ДО
     решения о пересборке. Разметка = побочный продукт обычной проверки
     (продуктовое требование постановки), не отдельная функция.
  4a. ЭТАП U4: в каждую запись разметки дополнительно кладётся `census`
     (слепок «сколько масок и какие» на момент правки, см. `_markup_census`) и
     `old_detector` (какой механизм поставил правленую маску). Это ЗНАМЕНАТЕЛЬ
     и ПРОВЕНАНС для метрик `app/report.py`: без слепка метрика по разметке
     старше суток осталась бы без знаменателя (разметка живёт 30 дней, сессия —
     24 часа). Детекцию и пересборку эти поля не затрагивают.
  4. Пересборка (`apply_pending_markup`) — единый примитив: КАЖДАЯ правка это
     «убрать старое вхождение маски (если было) + добавить новое (если не
     false_mask)», применяется последовательно к ОДНОЙ и той же сессии, что
     делает «граница»/«тип» составными операциями поверх «пропущено»/«ложная
     маска» без отдельного кода. Session ПЕРЕЗАПИСЫВАЕТСЯ (session_store.save_session
     под тем же session_id) с сохранением ИСХОДНОГО expires_at — правка не
     продлевает TTL хранения ПДн.
"""

import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import app_root  # noqa: E402

# src/ на пути — тот же приём, что в shifrator.py (плоские импорты — контракт проекта).
# В frozen-сборке это no-op (модули уже вкомпилированы), но безвреден.
_ROOT = app_root()
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(1, _SRC)

DEFAULT_CONFIG = os.path.join(_ROOT, "entity_types.yaml")

# ЭТАП U1b (2026-07-25): пересборка поставки на актуальном коде. Прошлая сборка
# («u1-desktop-packaging») сделана до влития E′/E″/S1/U2/U3/U4/S3 — метка обязана
# отличаться, иначе по футеру/логу/отчёту старую поставку не отличить от новой.
BUILD_MARK = "u1b-rebuild-20260725"


# --- ЭТАП T1: какие типы маскировать (набор + перекрытия пользователя) --------
def current_policy(config_path: str = None) -> dict:
    """Действующая настройка «что маскировать»: профиль + состав включённых
    типов. Читается из `~/.shifrator/settings.json` ПРИ КАЖДОМ вызове — файл
    правит человек, и перезапуск интерфейса ради смены набора не нужен.

    Возвращает {"profile", "enabled_types", "disabled_types", "enabled"} —
    первые три структурны (годятся в отчёт и в sidecar сессии), `enabled` —
    frozenset|None для `tokenizer.tokenize(..., enabled_types=...)`.
    """
    import type_policy

    cfg = config_path or DEFAULT_CONFIG
    # T1-UI шаг 1: однократное решение «обновление или первая установка». Файл
    # есть — не трогает ничего; см. type_policy.ensure_settings.
    type_policy.ensure_settings()
    settings = type_policy.load_settings()
    enabled = type_policy.enabled_types(cfg, settings=settings)
    return {
        "profile": settings["profile"],
        **type_policy.describe(cfg, enabled),
        "enabled": enabled,
    }


# --- Человекочитаемые названия типов ПДн (для юриста, не коды) ---------------
TYPE_LABELS = {
    "PERSON": "ФИО",
    "ORG": "Организация",
    "ADDRESS": "Адрес",
    "INN": "ИНН",
    "OGRN": "ОГРН/ОГРНИП",
    "KPP": "КПП",
    "BANK_ACCOUNT": "Банковский счёт",
    "BIK": "БИК",
    "PASSPORT": "Паспорт",
    "PHONE": "Телефон",
    "EMAIL": "E-mail",
    "SNILS": "СНИЛС",
    "BIRTHDATE": "Дата рождения",
    "SUM": "Сумма",
    "DATE": "Дата",
}

ZONE_KIND_LABELS = {
    "header": "колонтитул (верхний)",
    "footer": "колонтитул (нижний)",
    "footnote": "сноска",
    "endnote": "концевая сноска",
    "textbox": "надпись / текстовое поле",
    "nested_table": "вложенная таблица",
    "comment": "примечание (комментарий)",
}


# Типы, доступные при ручной разметке «Пометить пропущенное» (постановка U3,
# задача 1) — ровно список из постановки, в её порядке. BIK/SUM(порядок)/DATE
# намеренно не входят как отдельные пункты меню там, где постановка их не
# называла; DATE выключен в entity_types.yaml и здесь тоже не предлагается.
MARKUP_TYPES = [
    "PERSON", "ORG", "ADDRESS", "INN", "PASSPORT", "PHONE", "BANK_ACCOUNT",
    "EMAIL", "BIRTHDATE", "SNILS", "OGRN", "KPP", "SUM",
]


def type_label(entity_type: str) -> str:
    return TYPE_LABELS.get(entity_type, entity_type)


def settings_view(config_path: str = None) -> dict:
    """ЭТАП T1-UI: всё, что нужно экрану настроек, одним ответом.

    Наборы (имя + строка «что маскирует» человеческим языком) и ПОЛНЫЙ список
    типов с человеческими названиями и тремя признаками на каждый: входит ли в
    выбранный набор (`in_profile`), включён ли сейчас фактически (`enabled`),
    есть ли на нём ручное перекрытие (`override`, null если нет).

    Экран не хранит собственного представления о составе наборов — иначе он
    разошёлся бы с `type_policy` при добавлении типа (тот же класс рассинхрона,
    против которого заведён `src/pipeline.py`).
    """
    import type_policy

    cfg = config_path or DEFAULT_CONFIG
    type_policy.ensure_settings()
    settings = type_policy.load_settings()
    known = type_policy.known_types(cfg)
    profile = settings["profile"]
    overrides = {t: v for t, v in settings["types"].items() if t in known}

    base = type_policy.resolve(profile, {}, known=known)
    base_set = set(known) if base is None else set(base)
    enabled = type_policy.enabled_types(cfg, settings=settings)
    enabled_set = set(known) if enabled is None else set(enabled)

    return {
        "profile": profile,
        "profiles": [
            {"id": p, "label": type_policy.PROFILE_LABELS[p],
             "hint": type_policy.PROFILE_HINTS[p]}
            for p in type_policy.PROFILES
        ],
        # DATE на экран не выводится: его ДЕТЕКТОР выключен в entity_types.yaml
        # (`enabled: false`), масок этого типа не будет ни при какой галочке —
        # предложить её значило бы обещать невыполнимое. Из состава политики тип
        # при этом не выкинут (там он значим для набора «Максимум»).
        "types": [
            {"type": t, "label": type_label(t), "in_profile": t in base_set,
             "enabled": t in enabled_set, "override": overrides.get(t)}
            for t in known if t != "DATE"
        ],
        "settings_path": str(type_policy.settings_path()),
    }


def save_settings(profile: str, types: dict = None, config_path: str = None) -> dict:
    """Записать выбор экрана в ТОТ ЖЕ файл настроек, что читает механизм T1.

    Перекрытия чистятся от совпадающих с набором: перекрытие «включить то, что
    и так включено набором» — не выбор пользователя, а шум, который вдобавок
    залипнет при последующей смене набора. В файл идёт только реальное
    отличие от набора.
    """
    import type_policy

    cfg = config_path or DEFAULT_CONFIG
    known = type_policy.known_types(cfg)
    if profile not in type_policy.PROFILES:
        profile = type_policy.DEFAULT_PROFILE

    base = type_policy.resolve(profile, {}, known=known)
    base_set = set(known) if base is None else set(base)

    clean = {}
    for t, v in dict(types or {}).items():
        if t not in known:
            continue          # неизвестный тип с экрана не пишем (как и load_settings его игнорирует)
        if bool(v) != (t in base_set):
            clean[t] = bool(v)

    type_policy.save_settings(profile, clean)
    return settings_view(cfg)


def policy_summary(policy: dict, config_path: str = None) -> dict:
    """Строка «чем обезличено» для экрана результата (T1-UI, шаг 3).

    Показывает не только ЧТО маскировалось, но и — если набор не полный — что
    осталось В ТЕКСТЕ НАМЕРЕННО. Без этого юрист читает отсутствие маски как
    «программа не нашла», хотя это был его собственный выбор.
    """
    import type_policy

    profile = policy.get("profile")
    # DATE из перечня «осталось намеренно» исключён: его детектор выключен в
    # entity_types.yaml (`enabled: false`), маски этого типа не появятся ни при
    # каком наборе — обещать пользователю обратное было бы неправдой.
    disabled = [t for t in policy.get("disabled_types") or [] if t != "DATE"]
    return {
        "profile": profile,
        "profile_label": type_policy.PROFILE_LABELS.get(profile, profile),
        "is_full": not disabled,
        "enabled_labels": [type_label(t) for t in policy.get("enabled_types") or []],
        "disabled_labels": [type_label(t) for t in disabled],
    }


def markup_type_options() -> list[dict]:
    return [{"type": t, "label": TYPE_LABELS[t]} for t in MARKUP_TYPES]


def storage_dir():
    """Директория хранилища сессий CLI (~/.shifrator/sessions)."""
    from storage import default_storage_dir
    return default_storage_dir()


# --- Непрочитанные зоны -------------------------------------------------------
def scan_zones(path: str) -> list[dict]:
    """Список непрочитанных зон .docx в JSON-виде (пустой — читается целиком).

    Прокидывает наверх ooxml_core.OoxmlError на битом контейнере — вызывающий
    решает, как показать. Для .txt зон не бывает — возвращаем пустой список.
    """
    from unread_zones import scan_unread_zones

    if not path.lower().endswith(".docx"):
        return []
    zones = scan_unread_zones(path)
    return [
        {
            "kind": z.kind,
            "kind_label": ZONE_KIND_LABELS.get(z.kind, z.kind),
            "part": z.part,
            "char_count": z.char_count,
            "preview": z.text_preview,
        }
        for z in zones
    ]


# --- Подсветка: рендер сегмента в HTML с <mark> вокруг замен ------------------
def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _mark(entity_type: str, ri: int, inner_html: str, *, interactive: dict | None = None) -> str:
    """interactive (только для anon-панели, U3): координаты этого КОНКРЕТНОГО
    вхождения маски в исходном сегменте + сам токен — нужны экрану разметки,
    чтобы «щёлкнуть по маске» однозначно определял, какое именно вхождение
    редактируется (у одного токена таких вхождений может быть много)."""
    attrs = f'class="ent t-{entity_type}" data-ri="{ri}"'
    if interactive is not None:
        attrs += (
            f' data-seg="{_esc(interactive["seg"])}" data-start="{interactive["start"]}"'
            f' data-end="{interactive["end"]}" data-token="{_esc(interactive["token"])}"'
            f' data-etype="{_esc(entity_type)}" data-orig="{_esc(interactive["orig"])}"'
        )
    return f'<mark {attrs}>{inner_html}</mark>'


def _lit(seg_id: str, start: int, end: int, text: str) -> str:
    """U3: обёртка НЕзамаскированного фрагмента anon-панели — экран разметки
    находит по data-seg/data-s/data-e координаты ИСХОДНОГО сегмента для любого
    выделения мышью внутри этого фрагмента (см. wireMarkupSelection в index.html)."""
    if not text:
        return ""
    return f'<span class="lit" data-seg="{_esc(seg_id)}" data-s="{start}" data-e="{end}">{_esc(text)}</span>'


def _render_original(seg, ents_by_seg, tok_index):
    """Текст сегмента с исходными значениями, обёрнутыми в <mark>."""
    ents = sorted(ents_by_seg.get(seg.id, []), key=lambda e: e.start)
    out, pos, text = [], 0, seg.text
    for e in ents:
        if e.start < pos:      # страховка: после _resolve_overlaps не бывает
            continue
        out.append(_esc(text[pos:e.start]))
        out.append(_mark(e.entity_type, tok_index[e.token], _esc(text[e.start:e.end])))
        pos = e.end
    out.append(_esc(text[pos:]))
    return "".join(out)


def _render_anon(seg, ents_by_seg, tok_index):
    """Тот же сегмент, но значения заменены токеном (как в tokenizer._render_segment).
    U3: НЕзамаскированные фрагменты обёрнуты в `.lit` (координаты для разметки),
    маски несут координаты своего вхождения (для щелчка по существующей маске)."""
    ents = sorted(ents_by_seg.get(seg.id, []), key=lambda e: e.start)
    out, pos, text = [], 0, seg.text
    for e in ents:
        if e.start < pos:
            continue
        out.append(_lit(seg.id, pos, e.start, text[pos:e.start]))
        out.append(_mark(
            e.entity_type, tok_index[e.token], _esc(e.token),
            interactive={"seg": seg.id, "start": e.start, "end": e.end,
                         "token": e.token, "orig": e.original_text},
        ))
        pos = e.end
    out.append(_lit(seg.id, pos, len(text), text[pos:]))
    return "".join(out)


class EncryptRefused(Exception):
    """strict-режим: документ содержит непрочитанные зоны, работа не выполнена.

    Несёт список зон (JSON-вид), чтобы UI показал осознанный выбор, а не проглотил.
    """

    def __init__(self, zones: list[dict]):
        self.zones = zones
        super().__init__(f"Документ содержит {len(zones)} непрочитанных зон(ы)")


def _entities_from_session(session: dict) -> list:
    """Разворачивает формат сессии (одна запись на токен + occurrences) обратно в
    плоский список псевдо-Entity (один объект на КАЖДОЕ вхождение) — тот же вид,
    что tokenize() отдаёт до записи в сессию. Используется, чтобы рендер экрана
    проверки ВСЕГДА шёл из сессии (единственный источник истины, см. модульный
    docstring «этап U3, п.1») — и сразу после шифрации, и после ручной правки."""
    from types import SimpleNamespace

    out = []
    for rec in session["entities"]:
        for occ in rec["occurrences"]:
            out.append(SimpleNamespace(
                token=rec["token"], entity_type=rec["entity_type"],
                segment_id=occ["segment_id"], start=occ["start"], end=occ["end"],
                original_text=occ["surface"], spans=occ.get("spans"),
                canonical=rec.get("canonical"),
                # Провенанс (этап S1) — отсутствует (None) на сессиях до S1.
                detector=occ.get("detector", rec.get("detector")),
                group_key=occ.get("group_key", rec.get("group_key")),
            ))
    return out


def _render_state(doc, session: dict) -> dict:
    """original_html/anon_html/anon_text/replacements/entities_total/values_hidden
    из ТЕКУЩЕГО состояния сессии (после любых ручных правок) — единая точка,
    используемая run_encrypt (первый рендер) и apply_pending_markup (пересборка)."""
    from tokenizer import _assemble, _render_segment

    entities = _entities_from_session(session)

    tok_index: dict[str, int] = {}
    for e in entities:
        if e.token not in tok_index:
            tok_index[e.token] = len(tok_index)

    ents_by_seg: dict[str, list] = {}
    for e in entities:
        ents_by_seg.setdefault(e.segment_id, []).append(e)

    original_html = _assemble(doc, lambda s: _render_original(s, ents_by_seg, tok_index))
    anon_html = _assemble(doc, lambda s: _render_anon(s, ents_by_seg, tok_index))
    anon_text = _assemble(doc, lambda s: _render_segment(s, ents_by_seg.get(s.id, [])))

    first_by_token: dict[str, object] = {}
    count_by_token: dict[str, int] = {}
    for e in entities:
        count_by_token[e.token] = count_by_token.get(e.token, 0) + 1
        first_by_token.setdefault(e.token, e)

    replacements = []
    for token, ri in tok_index.items():
        e = first_by_token[token]
        replacements.append({
            "ri": ri,
            "token": token,
            "entity_type": e.entity_type,
            "type_label": type_label(e.entity_type),
            "original_text": e.original_text,
            "count": count_by_token[token],
        })
    replacements.sort(key=lambda r: (r["type_label"], r["original_text"]))

    return {
        "original_html": original_html,
        "anon_html": anon_html,
        "anon_text": anon_text,
        "replacements": replacements,
        "entities_total": len(entities),
        "values_hidden": len(tok_index),
    }


def run_encrypt(path: str, allow_lossy: bool = False, config_path: str = DEFAULT_CONFIG,
                 source_name: str | None = None, on_extracted=None) -> dict:
    """Полный конвейер шифрации + данные для ЭКРАНА ПРОВЕРКИ.

    Порядок вызовов идентичен shifrator.py::cmd_encrypt. Отличия:
      * непрочитанные зоны в strict-режиме поднимают EncryptRefused (а не sys.exit);
      * возвращаем исходный/обезличенный HTML с подсветкой, список замен и зоны.

    source_name — имя файла, каким его видел пользователь (сервер получает его из
    заголовка запроса; `path` — путь к временной копии с рандомным именем). Если не
    передан — берётся basename(path). Сохраняется в sidecar сессии (storage.save_session_meta)
    для списка сессий (Задача U2-3, "какой документ").

    on_extracted(doc) — необязательный колбэк, вызывается СРАЗУ после извлечения
    текста документа (задача U3-4, счётчик прогресса): число сегментов известно
    задолго до конца детекции, сервер использует это для индикатора «документ:
    N сегментов» ДО того, как ответ вообще готов (детекция — синхронная и самая
    долгая часть, инструментировать её стадии этой сессии нельзя — см. HANDOFF_U3).

    Исключения FileNotFoundError / ValueError (битый файл) / OoxmlError пробрасываются
    наверх — их переводит в человеческий текст вызывающий (server.py).
    """
    from extractor import extract
    from pipeline import run_detection
    from tokenizer import tokenize
    from storage import save_session, save_session_meta, save_doc_segments, load_session, default_storage_dir
    import json

    display_name = source_name or os.path.basename(path)

    doc = extract(path)   # FileNotFoundError / ValueError наружу
    if on_extracted is not None:
        on_extracted(doc)

    # Политика зон — та же, что в CLI: сканируем ПОСЛЕ extract, ДО save_session.
    zones = scan_zones(path)   # OoxmlError наружу
    if zones and not allow_lossy:
        raise EncryptRefused(zones)

    # --- Реальная детекция (ЕДИНЫЙ конвейер, никакой своей логики) ---
    # Порядок шагов — в pipeline.run_detection, ТА ЖЕ функция, что зовёт CLI. UI
    # больше не держит копию порядка и не может отстать от этапа (был инцидент, см.
    # archive/reports/HANDOFF_UI.md и src/pipeline.py).
    entities = run_detection(doc, config_path)
    # ЭТАП T1: детекция выше — ПОЛНАЯ, всеми слоями, независимо от настройки.
    # Фильтр типов живёт ВНУТРИ tokenize, ПОСЛЕ разрешения пересечений: выключенный
    # тип обязан отработать барьером, иначе поедут границы масок соседних,
    # ВКЛЮЧЁННЫХ типов (см. src/type_policy.py).
    policy = current_policy(config_path)
    _anon_text_unused, final_entities = tokenize(
        doc, entities, config_path, enabled_types=policy["enabled"],
    )

    session_id = save_session(final_entities, session_id=None, ttl_hours=24)
    save_session_meta(session_id, display_name, policy={
        "profile": policy["profile"],
        "enabled_types": policy["enabled_types"],
        "disabled_types": policy["disabled_types"],
    })
    # U3: сегменты исходного документа — нужны для пересборки после ручной
    # правки разметки, см. модульный docstring и storage.save_doc_segments.
    save_doc_segments(session_id, doc)

    store = default_storage_dir()

    # Рендерим из ТОЛЬКО ЧТО сохранённой сессии (не из final_entities напрямую) —
    # тот же путь, что будет использован после каждой ручной правки (U3, п.1).
    session = load_session(session_id)
    state = _render_state(doc, session)
    (store / f"{session_id}.txt").write_text(state["anon_text"], encoding="utf-8")

    # lossy: sidecar с СЫРЫМ текстом зон — ровно как в CLI (локально, в LLM не идёт).
    if zones and allow_lossy:
        from unread_zones import scan_unread_zones, zones_to_json
        sidecar = store / f"{session_id}.unread.json"
        sidecar.write_text(
            json.dumps(
                {"session_id": session_id, "source_path": os.path.abspath(path),
                 "lossy": True, "zones": zones_to_json(scan_unread_zones(path))},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

    return {
        "session_id": session_id,
        "source_name": display_name,
        **state,
        "zones": zones,
        "lossy": bool(zones and allow_lossy),
        "storage_dir": str(store),
        # T1-UI шаг 3: чем обезличено — слепок МОМЕНТА ШИФРАЦИИ (тот же, что ушёл
        # в sidecar сессии), а не текущего состояния файла настроек.
        "policy": policy_summary(policy, config_path),
    }


def run_decrypt(session_id: str, llm_text: str) -> dict:
    """Восстановление: detokenize(text, session_id) поверх реального хранилища.

    Типизированные ошибки session_store переводятся в человеческий текст ВЫШЕ
    (server.py) — здесь пробрасываем как есть, чтобы не терять их тип.
    """
    from detokenizer import detokenize

    restored, unresolved = detokenize(llm_text, session_id)
    return {"restored": restored, "unresolved": unresolved}


# ============================================================================
# ЭТАП U3 — разметка экрана проверки (задачи 1-3 постановки).
#
# Модель: КАЖДОЕ действие пользователя (пометить пропущенное / снять ложную
# маску / поправить границу / поправить тип) сразу сохраняется как запись
# разметки (storage.save_markup) — это происходит ВСЕГДА, вне зависимости от
# того, нажмёт ли пользователь «Применить и пересобрать» (задача 2, разметка —
# побочный продукт проверки, не отдельное действие). Пересборка документа
# (задача 3, решение — см. HANDOFF_U3 §3) — отдельный шаг, применяющий ВСЕ ещё
# не применённые записи пачкой к сессии.
#
# Два примитива, из которых собраны все 4 вида правки:
#   _add_missed(...)      — добавить новое вхождение маски (новый или тот же тип)
#   _remove_occurrence(...) — убрать ОДНО конкретное вхождение существующей маски
# missed = add; false_mask = remove; boundary/type = remove(старое) + add(новое).
# ============================================================================

def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat()


def _find_segment(doc, segment_id: str):
    for s in doc.segments:
        if s.id == segment_id:
            return s
    return None


def _validate_missed(doc, session: dict, segment_id: str, start: int, end: int,
                      entity_type: str, config_path: str):
    """Проверка ДО добавления новой маски (используется и предпросмотром
    /api/markup/mark-missed, и пересборкой). Кидает ValueError с человеческим
    текстом; ничего не мутирует. Возвращает (segment, значение_фрагмента)."""
    from tokenizer import _load_token_prefixes

    seg = _find_segment(doc, segment_id)
    if seg is None:
        raise ValueError("Этот фрагмент документа не найден — обновите страницу и попробуйте снова.")
    if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end <= len(seg.text)):
        raise ValueError("Некорректное выделение.")
    value = seg.text[start:end]
    if not value.strip():
        raise ValueError("Пустое выделение — выберите хотя бы один видимый символ.")

    prefixes = _load_token_prefixes(config_path)
    if entity_type not in prefixes:
        raise ValueError(f"Неизвестный тип персональных данных: {entity_type}")

    for rec in session["entities"]:
        for occ in rec["occurrences"]:
            if occ["segment_id"] == segment_id and max(occ["start"], start) < min(occ["end"], end):
                raise ValueError(
                    "Выделение пересекает уже существующую маску — сначала снимите её "
                    "или воспользуйтесь «Поправить границу»."
                )
    return seg, value


def _next_token(prefix: str, session: dict) -> str:
    import re

    pat = re.compile(r"^\[" + re.escape(prefix) + r"_(\d+)\]$")
    n = 0
    for rec in session["entities"]:
        m = pat.match(rec["token"])
        if m:
            n = max(n, int(m.group(1)))
    return f"[{prefix}_{n + 1}]"


def _add_missed(doc, session: dict, segment_id: str, start: int, end: int,
                 entity_type: str, config_path: str) -> tuple[str, str]:
    """Мутирует session["entities"] на месте: добавляет НОВОЕ вхождение маски.
    Возвращает (token, value)."""
    from tokenizer import _load_token_prefixes

    _, value = _validate_missed(doc, session, segment_id, start, end, entity_type, config_path)
    prefix = _load_token_prefixes(config_path)[entity_type]
    token = _next_token(prefix, session)
    session["entities"].append({
        "token": token, "entity_type": entity_type, "original_text": value,
        "segment_id": segment_id, "canonical": None,
        # Провенанс (этап S1): вручную добавленная маска отличима от автоматической.
        "detector": "manual", "group_key": None,
        "occurrences": [{"segment_id": segment_id, "start": start, "end": end,
                          "spans": None, "surface": value,
                          "detector": "manual", "group_key": None}],
    })
    return token, value


def _remove_occurrence(session: dict, token: str, segment_id: str, start: int, end: int) -> dict:
    """Мутирует session["entities"] на месте: убирает ОДНО вхождение токена по
    точным координатам. Если это было последнее вхождение — токен убирается
    целиком (перестаёт эмитироваться, при decrypt даст unresolved, если
    попадётся в тексте — ожидаемо, других вхождений у него больше нет)."""
    for rec in session["entities"]:
        if rec["token"] != token:
            continue
        for i, occ in enumerate(rec["occurrences"]):
            if occ["segment_id"] == segment_id and occ["start"] == start and occ["end"] == end:
                removed = rec["occurrences"].pop(i)
                if not rec["occurrences"]:
                    session["entities"].remove(rec)
                return removed
        raise ValueError("Это вхождение маски уже изменено или отсутствует — обновите страницу.")
    raise ValueError(f"Маска {token} не найдена в сессии — обновите страницу.")


def _find_occurrence(session: dict, token: str, segment_id: str, start: int, end: int) -> tuple[dict, dict]:
    for rec in session["entities"]:
        if rec["token"] != token:
            continue
        for occ in rec["occurrences"]:
            if occ["segment_id"] == segment_id and occ["start"] == start and occ["end"] == end:
                return rec, occ
    raise ValueError("Эта маска уже не существует в текущей сессии — обновите страницу.")


def mark_missed(session_id: str, segment_id: str, start: int, end: int,
                 entity_type: str, config_path: str = DEFAULT_CONFIG) -> dict:
    """Пользователь выделил ПРОПУЩЕННЫЙ фрагмент на обезличенной панели и выбрал
    тип. Сохраняет запись разметки (kind="missed"), НЕ пересобирает документ
    сразу (см. модульный docstring этапа U3)."""
    from storage import load_session, load_doc_segments, save_markup

    session = load_session(session_id)          # SessionNotFoundError/Expired наружу
    doc = load_doc_segments(session_id)          # FileNotFoundError наружу (старая сессия)
    _, value = _validate_missed(doc, session, segment_id, start, end, entity_type, config_path)

    markup_id = save_markup(session_id, {
        "kind": "missed", "entity_type": entity_type,
        "segment_id": segment_id, "start": start, "end": end, "value": value,
        "old_token": None, "old_segment_id": None, "old_start": None, "old_end": None,
        "old_entity_type": None, "old_detector": None,
        "created_at": _now_iso(), "build_mark": BUILD_MARK, "applied": False, "apply_error": None,
        "census": _markup_census(session_id, session),
    })
    return {"markup_id": markup_id, "value": value}


def mark_false_positive(session_id: str, segment_id: str, start: int, end: int, token: str) -> dict:
    """Пользователь щёлкнул по СУЩЕСТВУЮЩЕЙ маске и сказал «это не ПДн»."""
    from storage import load_session, save_markup

    session = load_session(session_id)
    rec, occ = _find_occurrence(session, token, segment_id, start, end)

    markup_id = save_markup(session_id, {
        "kind": "false_mask", "entity_type": rec["entity_type"],
        "segment_id": segment_id, "start": start, "end": end, "value": occ["surface"],
        "old_token": token, "old_segment_id": segment_id, "old_start": start, "old_end": end,
        "old_entity_type": rec["entity_type"],
        # U4: КАКОЙ механизм поставил отклонённую маску — без этого отчёт не может
        # сказать «ложные срабатывания дал regex, а не NER» (приёмка U4-4).
        "old_detector": occ.get("detector", rec.get("detector")),
        "created_at": _now_iso(), "build_mark": BUILD_MARK, "applied": False, "apply_error": None,
        "census": _markup_census(session_id, session),
    })
    return {"markup_id": markup_id}


def mark_replace(session_id: str, old_token: str, old_segment_id: str, old_start: int, old_end: int,
                  new_segment_id: str, new_start: int, new_end: int, entity_type: str,
                  config_path: str = DEFAULT_CONFIG) -> dict:
    """Поправить границу (new_* другой диапазон, entity_type тот же) или поправить
    тип (new_* совпадает со старым диапазоном, entity_type другой) — один и тот
    же примитив: убрать старое вхождение, добавить новое. kind вычисляется по
    тому, поменялся ли entity_type."""
    import copy

    from storage import load_session, load_doc_segments, save_markup

    session = load_session(session_id)
    doc = load_doc_segments(session_id)
    old_rec, old_occ = _find_occurrence(session, old_token, old_segment_id, old_start, old_end)

    # Валидация новой границы — на КОПИИ сессии без старого вхождения (граница
    # имеет право пересекаться сама с собой/сузиться на месте старой маски).
    tmp_session = copy.deepcopy(session)
    _remove_occurrence(tmp_session, old_token, old_segment_id, old_start, old_end)
    _, value = _validate_missed(doc, tmp_session, new_segment_id, new_start, new_end, entity_type, config_path)

    kind = "type" if entity_type != old_rec["entity_type"] else "boundary"
    markup_id = save_markup(session_id, {
        "kind": kind, "entity_type": entity_type,
        "segment_id": new_segment_id, "start": new_start, "end": new_end, "value": value,
        "old_token": old_token, "old_segment_id": old_segment_id,
        "old_start": old_start, "old_end": old_end, "old_entity_type": old_rec["entity_type"],
        "old_detector": old_occ.get("detector", old_rec.get("detector")),
        "created_at": _now_iso(), "build_mark": BUILD_MARK, "applied": False, "apply_error": None,
        "census": _markup_census(session_id, session),
    })
    return {"markup_id": markup_id, "value": value, "kind": kind}


def _markup_census(session_id: str, session: dict) -> dict | None:
    """ЭТАП U4: компактный слепок «сколько масок и какие» на момент правки.

    Кладётся в КАЖДУЮ запись разметки, потому что знаменатель метрик (сколько
    масок поставил движок) — это состояние СЕССИИ, а сессия живёт 24 часа против
    30 дней у разметки (этап S1). Без слепка любая метрика по разметке старше
    суток осталась бы без знаменателя. Слепок — только числа, ПДн в нём нет.

    Отчёт берёт слепок САМОЙ РАННЕЙ записи сессии (состояние до ручных правок,
    т.е. чистая автоматика) — см. app/report.py.

    Сбой (нет report.py в сборке, неожиданный формат сессии) не должен ронять
    сохранение правки: разметка важнее метрики, запись просто останется без
    слепка, и отчёт честно посчитает документ «без знаменателя».

    ЭТАП T1: в слепок добавлен `policy` — СОСТАВ ВКЛЮЧЁННЫХ ТИПОВ, которым
    документ маскировался (из sidecar сессии, `storage.load_session_policy`).
    Без него числа отчёта двусмысленны: ноль масок типа значит либо «детектор не
    нашёл», либо «тип был выключен пользователем». Слепок берётся МОМЕНТА
    ШИФРАЦИИ, а не момента отчёта — настройка между ними могла измениться.
    """
    try:
        import report
        from storage import load_session_policy

        census = report.census_from_session(session)
        census["policy"] = load_session_policy(session_id)
        return census
    except Exception:  # noqa: BLE001 — метрика не имеет права ломать разметку
        return None


def list_markup_entries(session_id: str) -> list[dict]:
    from storage import list_markup
    return list_markup(session_id)


def update_markup_entry(session_id: str, markup_id: str, entity_type: str) -> None:
    from storage import list_markup, update_markup

    entries = list_markup(session_id)
    entry = next((e for e in entries if e["id"] == markup_id), None)
    if entry is None:
        raise ValueError("Запись разметки не найдена.")
    if entry.get("applied"):
        raise ValueError("Эта правка уже применена к документу — редактировать тип нельзя, снимите/поправьте её отдельной правкой.")
    if entity_type not in TYPE_LABELS:
        raise ValueError(f"Неизвестный тип: {entity_type}")
    if not update_markup(session_id, markup_id, entity_type=entity_type):
        raise ValueError("Запись разметки не найдена.")


def delete_markup_entry(session_id: str, markup_id: str) -> dict:
    from storage import delete_markup, list_markup

    entries = list_markup(session_id)
    entry = next((e for e in entries if e["id"] == markup_id), None)
    was_applied = bool(entry and entry.get("applied"))
    deleted = delete_markup(session_id, markup_id)
    return {"deleted": deleted, "was_applied": was_applied}


def apply_pending_markup(session_id: str, config_path: str = DEFAULT_CONFIG) -> dict:
    """«Применить правки и пересобрать» (задача 3). Применяет ВСЕ ещё не
    применённые записи разметки к сессии, в порядке создания; правка, которая
    больше не валидна к моменту применения (напр. пересекается с другой,
    применённой раньше в этой же пачке), помечается applied=False с текстом
    ошибки в apply_error — ОСТАЛЬНЫЕ правки это не блокирует (частичный успех
    предпочтительнее полного отказа: пользователь увидит, что именно не
    применилось, и почему).

    Возвращает {**новое_состояние_рендера, "results": [...]}, results — по одной
    записи на КАЖДУЮ обработанную запись разметки (id/kind/applied/apply_error) —
    фронт показывает, что удалось, что нет.
    """
    from datetime import datetime

    from storage import (
        load_session, load_doc_segments, list_markup, default_storage_dir,
        replace_session_entities,
    )
    from models import Entity

    session = load_session(session_id)
    doc = load_doc_segments(session_id)
    entries = list_markup(session_id)
    pending = [e for e in entries if not e.get("applied")]

    changed = False
    results = []
    for entry in pending:
        try:
            if entry.get("old_token"):
                _remove_occurrence(session, entry["old_token"], entry["old_segment_id"],
                                    entry["old_start"], entry["old_end"])
            if entry["kind"] != "false_mask":
                _add_missed(doc, session, entry["segment_id"], entry["start"], entry["end"],
                            entry["entity_type"], config_path)
            entry["applied"] = True
            entry["apply_error"] = None
            changed = True
        except Exception as exc:  # noqa: BLE001 — переводим в текст записи, не роняем пачку
            entry["applied"] = False
            entry["apply_error"] = str(exc)
        results.append({"id": entry["id"], "kind": entry["kind"], "applied": entry["applied"],
                         "apply_error": entry["apply_error"], "entity_type": entry["entity_type"],
                         "value": entry.get("value")})

    # Пишем актуализированные (applied/apply_error) записи разметки в любом
    # случае — даже если ничего не применилось, статус попытки должен сохраниться.
    from storage import update_markup
    for entry in pending:
        update_markup(session_id, entry["id"], applied=entry["applied"], apply_error=entry["apply_error"])

    if changed:
        entities: list[Entity] = []
        for rec in session["entities"]:
            for occ in rec["occurrences"]:
                spans = [tuple(sp) for sp in occ["spans"]] if occ.get("spans") else None
                # Провенанс (этап S1): КАЖДОЕ вхождение несёт СВОЙ реальный detector/
                # group_key (regex/ner для уже стоявших масок, "manual" для тех, что
                # добавила разметка), а не единый "manual" на ВСЮ сессию — иначе любая
                # пересборка стирала бы провенанс всех ранее автоматических масок.
                # occ.get(...) с fallback на None — старые occurrences (сессия
                # сохранена до этапа S1) провенанса не несут.
                entities.append(Entity(
                    id=f"ui-{session_id}-{len(entities)}", segment_id=occ["segment_id"],
                    start=occ["start"], end=occ["end"], original_text=occ["surface"],
                    entity_type=rec["entity_type"], detector=occ.get("detector"), confidence=1.0,
                    token=rec["token"], spans=spans, group_key=occ.get("group_key"),
                    canonical=rec.get("canonical"),
                ))
        expires_at = datetime.fromisoformat(session["expires_at"])
        replace_session_entities(session_id, entities, expires_at)
        session = load_session(session_id)  # перечитываем — единый источник истины (п.1)

        store = default_storage_dir()
        state = _render_state(doc, session)
        (store / f"{session_id}.txt").write_text(state["anon_text"], encoding="utf-8")
    else:
        state = _render_state(doc, session)

    return {**state, "session_id": session_id, "results": results}
