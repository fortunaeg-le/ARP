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

BUILD_MARK = "u1-desktop-packaging"


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


def type_label(entity_type: str) -> str:
    return TYPE_LABELS.get(entity_type, entity_type)


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


def _mark(entity_type: str, ri: int, inner_html: str) -> str:
    return f'<mark class="ent t-{entity_type}" data-ri="{ri}">{inner_html}</mark>'


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
    """Тот же сегмент, но значения заменены токеном (как в tokenizer._render_segment)."""
    ents = sorted(ents_by_seg.get(seg.id, []), key=lambda e: e.start)
    out, pos, text = [], 0, seg.text
    for e in ents:
        if e.start < pos:
            continue
        out.append(_esc(text[pos:e.start]))
        out.append(_mark(e.entity_type, tok_index[e.token], _esc(e.token)))
        pos = e.end
    out.append(_esc(text[pos:]))
    return "".join(out)


class EncryptRefused(Exception):
    """strict-режим: документ содержит непрочитанные зоны, работа не выполнена.

    Несёт список зон (JSON-вид), чтобы UI показал осознанный выбор, а не проглотил.
    """

    def __init__(self, zones: list[dict]):
        self.zones = zones
        super().__init__(f"Документ содержит {len(zones)} непрочитанных зон(ы)")


def run_encrypt(path: str, allow_lossy: bool = False, config_path: str = DEFAULT_CONFIG) -> dict:
    """Полный конвейер шифрации + данные для ЭКРАНА ПРОВЕРКИ.

    Порядок вызовов идентичен shifrator.py::cmd_encrypt. Отличия:
      * непрочитанные зоны в strict-режиме поднимают EncryptRefused (а не sys.exit);
      * возвращаем исходный/обезличенный HTML с подсветкой, список замен и зоны.

    Исключения FileNotFoundError / ValueError (битый файл) / OoxmlError пробрасываются
    наверх — их переводит в человеческий текст вызывающий (server.py).
    """
    from extractor import extract
    from pipeline import run_detection
    from tokenizer import tokenize, _assemble
    from storage import save_session, default_storage_dir
    import json

    doc = extract(path)   # FileNotFoundError / ValueError наружу

    # Политика зон — та же, что в CLI: сканируем ПОСЛЕ extract, ДО save_session.
    zones = scan_zones(path)   # OoxmlError наружу
    if zones and not allow_lossy:
        raise EncryptRefused(zones)

    # --- Реальная детекция (ЕДИНЫЙ конвейер, никакой своей логики) ---
    # Порядок шагов — в pipeline.run_detection, ТА ЖЕ функция, что зовёт CLI. UI
    # больше не держит копию порядка и не может отстать от этапа (был инцидент, см.
    # archive/reports/HANDOFF_UI.md и src/pipeline.py).
    entities = run_detection(doc, config_path)
    anon_text, final_entities = tokenize(doc, entities, config_path)

    session_id = save_session(final_entities, session_id=None, ttl_hours=24)

    store = default_storage_dir()
    (store / f"{session_id}.txt").write_text(anon_text, encoding="utf-8")

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

    # --- Данные для экрана проверки ---
    # Уникальный токен -> индекс замены (порядок появления в kept).
    tok_index: dict[str, int] = {}
    for e in final_entities:
        if e.token not in tok_index:
            tok_index[e.token] = len(tok_index)

    ents_by_seg: dict[str, list] = {}
    for e in final_entities:
        ents_by_seg.setdefault(e.segment_id, []).append(e)

    original_html = _assemble(doc, lambda s: _render_original(s, ents_by_seg, tok_index))
    anon_html = _assemble(doc, lambda s: _render_anon(s, ents_by_seg, tok_index))

    # Список замен: одна запись на уникальный токен (совпадает со тем, что хранит
    # session_store — дедуп по token). count — сколько раз встретилось в документе.
    first_by_token: dict[str, object] = {}
    count_by_token: dict[str, int] = {}
    for e in final_entities:
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
        "session_id": session_id,
        "source_name": os.path.basename(path),
        "original_html": original_html,
        "anon_html": anon_html,
        "anon_text": anon_text,
        "replacements": replacements,
        "entities_total": len(final_entities),
        "values_hidden": len(tok_index),
        "zones": zones,
        "lossy": bool(zones and allow_lossy),
        "storage_dir": str(store),
    }


def run_decrypt(session_id: str, llm_text: str) -> dict:
    """Восстановление: detokenize(text, session_id) поверх реального хранилища.

    Типизированные ошибки session_store переводятся в человеческий текст ВЫШЕ
    (server.py) — здесь пробрасываем как есть, чтобы не терять их тип.
    """
    from detokenizer import detokenize

    restored, unresolved = detokenize(llm_text, session_id)
    return {"restored": restored, "unresolved": unresolved}
