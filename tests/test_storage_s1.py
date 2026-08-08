"""Этап S1 (гигиена данных и жизненный цикл разметки) — тесты storage.py.

Три независимые правки в слое хранения, детекция не тронута:
  1. Разметка (U3) переживает удаление/просрочку сессии — отдельное хранилище,
     своя политика срока, явное удаление (отдельно/всё разом), миграция старых
     файлов.
  2. {sid}.doc.json не сериализует служебные кеши видов текста детекции —
     меньше лишних копий ПДн на диске, round-trip не страдает.
  3. Провенанс маски (`detector`/`group_key`) сохраняется в сессии на всех
     путях (автоматическая детекция И ручная разметка U3), старые сессии без
     поля продолжают читаться.

Изоляция: `storage.default_storage_dir`/`default_markup_dir` подменяются на
tmp_path через monkeypatch (тот же приём, что и остальной проект — никогда не
трогаем реальный ~/.shifrator).
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
for p in (str(_ROOT / "src"), str(_ROOT / "app")):
    if p not in sys.path:
        sys.path.insert(0, p)

import storage  # noqa: E402
import session_store  # noqa: E402
from models import Entity  # noqa: E402


@pytest.fixture
def iso_store(tmp_path, monkeypatch):
    """Подменяет хранилище сессий на изолированную директорию под tmp_path —
    реальный профиль (~/.shifrator) не трогаем.

    ДВЕ точки подмены нужны, потому что storage.py-фасад НЕ форвардит
    storage_dir в session_store (`save_session`/`load_session`/`purge_expired`
    зовут session_store без override — полагаются на её модульный дефолт), а
    остальные функции storage.py (`save_doc_segments`, `default_markup_dir`,
    сайдкар-циклы `delete_session`/`purge_expired`) читают `default_storage_dir()`
    ИЗ СВОЕГО модуля. Патчим оба места на ОДИН и тот же путь."""
    sessions_dir = tmp_path / "sessions"
    markup_dir = tmp_path / "markup"
    monkeypatch.setattr(session_store, "default_storage_dir", lambda: sessions_dir)
    monkeypatch.setattr(storage, "default_storage_dir", lambda: sessions_dir)
    return sessions_dir, markup_dir


def _entity(token="[PERSON_1]", text="Иванов", detector="ner", group_key="PER:0",
            entity_type="PERSON", start=0, end=6, segment_id="p0"):
    return Entity(
        id="e1", segment_id=segment_id, start=start, end=end, original_text=text,
        entity_type=entity_type, detector=detector, confidence=1.0, token=token,
        group_key=group_key, canonical=text,
    )


# --------------------------------------------------------------------------- #
# Задача 1 — разметка переживает сессию.
# --------------------------------------------------------------------------- #

def test_markup_survives_session_deletion(iso_store):
    sessions_dir, markup_dir = iso_store
    sid = storage.save_session([_entity()])
    storage.save_markup(sid, {"kind": "missed", "entity_type": "PERSON", "segment_id": "p0",
                               "start": 0, "end": 6, "value": "Иванов",
                               "created_at": datetime.now().astimezone().isoformat()})
    assert storage.list_markup(sid)

    storage.delete_session(sid)  # delete_markup=False по умолчанию

    with pytest.raises(storage.SessionNotFoundError):
        storage.load_session(sid)
    assert storage.list_markup(sid), "разметка должна остаться после удаления сессии"


def test_markup_survives_purge_expired_of_session(iso_store):
    sessions_dir, markup_dir = iso_store
    sid = storage.save_session([_entity()], ttl_hours=0)  # уже просрочена
    storage.save_markup(sid, {"kind": "missed", "entity_type": "PERSON", "segment_id": "p0",
                               "start": 0, "end": 6, "value": "Иванов",
                               "created_at": datetime.now().astimezone().isoformat()})

    removed = storage.purge_expired()
    assert removed == 1
    assert storage.list_markup(sid), "purge_expired сессий не должен трогать разметку"


def test_delete_session_with_markup_flag_removes_both(iso_store):
    sid = storage.save_session([_entity()])
    storage.save_markup(sid, {"kind": "missed", "entity_type": "PERSON", "segment_id": "p0",
                               "start": 0, "end": 6, "value": "X",
                               "created_at": datetime.now().astimezone().isoformat()})
    storage.delete_session(sid, delete_markup=True)
    assert storage.list_markup(sid) == []


def test_delete_session_markup_standalone_action(iso_store):
    """Приёмка 2: удалить разметку одной сессии отдельным действием."""
    sid = storage.save_session([_entity()])
    storage.save_markup(sid, {"kind": "missed", "entity_type": "PERSON", "segment_id": "p0",
                               "start": 0, "end": 6, "value": "X",
                               "created_at": datetime.now().astimezone().isoformat()})
    assert storage.delete_session_markup(sid) is True
    assert storage.list_markup(sid) == []
    # сессия сама осталась нетронутой
    storage.load_session(sid)
    assert storage.delete_session_markup(sid) is False  # уже нет — второй раз не найдено


def test_delete_all_markup(iso_store):
    """Приёмка 2: «удалить всю накопленную разметку» одним действием."""
    sid1 = storage.save_session([_entity()])
    sid2 = storage.save_session([_entity(token="[ORG_1]", entity_type="ORG")])
    for sid in (sid1, sid2):
        storage.save_markup(sid, {"kind": "missed", "entity_type": "PERSON", "segment_id": "p0",
                                   "start": 0, "end": 6, "value": "X",
                                   "created_at": datetime.now().astimezone().isoformat()})
    removed = storage.delete_all_markup()
    assert removed == 2
    assert storage.list_markup(sid1) == []
    assert storage.list_markup(sid2) == []
    # сессии не тронуты
    storage.load_session(sid1)
    storage.load_session(sid2)


def test_markup_own_retention_not_24h(iso_store):
    """Разметка живёт СВОИМ сроком, отличным от 24ч TTL сессии."""
    sid = storage.save_session([_entity()])
    old = (datetime.now().astimezone() - timedelta(days=storage._MARKUP_TTL_DAYS + 1)).isoformat()
    fresh = datetime.now().astimezone().isoformat()
    storage.save_markup(sid, {"kind": "missed", "entity_type": "PERSON", "segment_id": "p0",
                               "start": 0, "end": 6, "value": "old", "created_at": old})
    storage.save_markup(sid, {"kind": "missed", "entity_type": "PERSON", "segment_id": "p0",
                               "start": 7, "end": 12, "value": "fresh", "created_at": fresh})

    # запись старше TTL разметки не пропадает раньше времени при просрочке
    # сессии (24ч) — только собственный purge_expired_markup её тронет:
    removed = storage.purge_expired_markup()
    assert removed == 1
    remaining = storage.list_markup(sid)
    # ЭТАП STORE ч.4: сырого `value` в записи больше нет — уцелевшую отличаем
    # по границам, а заодно требуем, чтобы значение там и не появилось.
    assert len(remaining) == 1
    assert remaining[0]["start"] == 7 and remaining[0]["end"] == 12
    assert "value" not in remaining[0]


def test_migrate_legacy_markup_moves_and_merges(iso_store):
    """Приёмка 3: старая разметка (до S1, в директории сессий) переезжает, не теряясь."""
    sessions_dir, markup_dir = iso_store
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sid = "11111111-1111-1111-1111-111111111111"
    legacy_entries = [{"id": "a1", "kind": "missed", "entity_type": "PERSON",
                        "segment_id": "p0", "start": 0, "end": 3, "value": "old",
                        "created_at": datetime.now().astimezone().isoformat()}]
    (sessions_dir / f"{sid}.markup.json").write_text(
        json.dumps(legacy_entries, ensure_ascii=False), encoding="utf-8"
    )

    migrated = storage.migrate_legacy_markup()
    assert migrated == 1
    assert not (sessions_dir / f"{sid}.markup.json").exists()
    entries = storage.list_markup(sid)
    assert len(entries) == 1 and entries[0]["value"] == "old"

    # идемпотентна
    assert storage.migrate_legacy_markup() == 0


def test_markup_summary(iso_store):
    sid = storage.save_session([_entity()])
    empty = storage.markup_summary()
    assert empty == {"sessions": 0, "entries": 0, "oldest_created_at": None,
                      "ttl_days": storage._MARKUP_TTL_DAYS}
    storage.save_markup(sid, {"kind": "missed", "entity_type": "PERSON", "segment_id": "p0",
                               "start": 0, "end": 6, "value": "X",
                               "created_at": "2020-01-01T00:00:00+00:00"})
    s = storage.markup_summary()
    assert s["sessions"] == 1 and s["entries"] == 1
    assert s["oldest_created_at"] == "2020-01-01T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Задача 2 — {sid}.doc.json не раздут служебными кешами детекции.
# --------------------------------------------------------------------------- #

class _FakeDoc:
    def __init__(self, segments):
        self.segments = segments
        self.source_format = "txt"


def test_doc_segments_strips_detection_caches(iso_store):
    from models import TextSegment

    text = "Иванов Иван Иванович проживает по адресу." * 3
    seg = TextSegment(
        id="p0", text=text, source_type="txt_line",
        metadata={
            "paragraph_index": 0,
            "detection_text": text,  # копия текста (регистро-нормал.)
            "_norm_cache": (text, list(range(len(text)))),
            "_anchor_search_cache": text,
            "_per_search_cache": text,
        },
    )
    sid = storage.save_session([])
    storage.save_doc_segments(sid, _FakeDoc([seg]))

    doc_path = storage._doc_path(storage.default_storage_dir(), sid)
    # ЭТАП STORE: файл на диске зашифрован — сначала убеждаемся В ЭТОМ (текст
    # сегмента не обязан находиться в байтах файла), потом разбираем содержимое.
    raw_bytes = doc_path.read_bytes()
    assert raw_bytes.startswith(b"SHFR-ENC-1\n"), "{sid}.doc.json обязан быть зашифрован"
    assert text.encode("utf-8") not in raw_bytes, "исходный текст найден в файле открытым"
    data = storage._read_encrypted_json(doc_path)
    raw = json.dumps(data, ensure_ascii=False)
    md = data["segments"][0]["metadata"]
    assert "paragraph_index" in md  # структурное поле — осталось
    for k in ("detection_text", "_norm_cache", "_anchor_search_cache", "_per_search_cache"):
        assert k not in md, f"{k} должен быть вырезан — служебный кеш детекции"

    # файл заметно меньше, чем при наивной сериализации ВСЕХ полей metadata как есть
    naive_size = len(json.dumps({
        "source_format": "txt",
        "segments": [{"id": seg.id, "text": seg.text, "source_type": seg.source_type,
                      "metadata": seg.metadata}],
    }, ensure_ascii=False))
    assert len(raw) < naive_size * 0.5


def test_doc_segments_roundtrip_survives_cache_strip(iso_store):
    """Round-trip: пересборка через app/core.py-подобный путь (load_doc_segments +
    добавление ручной маски) не должна зависеть от вырезанных кешей — если
    когда-нибудь начнёт от них зависеть, этот тест упадёт первым."""
    from models import TextSegment

    text = "Пропущенное имя: Сидоров тут."
    seg = TextSegment(
        id="l0", text=text, source_type="txt_line",
        metadata={
            "detection_text": text,
            "_norm_cache": (text, list(range(len(text)))),
            "_anchor_search_cache": text,
            "_per_search_cache": text,
        },
    )
    sid = storage.save_session([])
    storage.save_doc_segments(sid, _FakeDoc([seg]))

    doc = storage.load_doc_segments(sid)
    restored_seg = doc.segments[0]
    assert restored_seg.text == text  # единственная нужная копия — на месте
    # значение, вырезаемое РУЧНОЙ разметкой, извлекается из seg.text напрямую —
    # ни один из вырезанных кешей для этого не требуется:
    start, end = text.index("Сидоров"), text.index("Сидоров") + len("Сидоров")
    assert restored_seg.text[start:end] == "Сидоров"


# --------------------------------------------------------------------------- #
# Задача 3 — провенанс маски.
# --------------------------------------------------------------------------- #

def test_session_persists_detector_and_group_key(iso_store):
    sid = storage.save_session([_entity(detector="ner", group_key="PER:5")])
    data = storage.load_session(sid)
    rec = data["entities"][0]
    assert rec["detector"] == "ner"
    assert rec["group_key"] == "PER:5"
    assert rec["occurrences"][0]["detector"] == "ner"
    assert rec["occurrences"][0]["group_key"] == "PER:5"


def test_session_persists_manual_detector(iso_store):
    """Ручная маска (detector='manual') отличима от автоматической в самой сессии
    (то же значение, что app/core.py::_add_missed кладёт в запись разметки)."""
    e = _entity(detector="manual", group_key=None)
    sid = storage.save_session([e])
    data = storage.load_session(sid)
    assert data["entities"][0]["detector"] == "manual"
    assert data["entities"][0]["group_key"] is None


def test_old_session_without_provenance_still_loads(iso_store):
    """Обратная совместимость: сессия формата ДО этапа S1 (без detector/group_key
    в JSON) продолжает читаться, поле опционально."""
    sessions_dir, _markup_dir = iso_store

    # Собираем legacy-формат вручную (без detector/group_key), шифруем тем же ключом.
    from cryptography.fernet import Fernet
    sessions_dir.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    (sessions_dir / "key.bin").write_bytes(key)
    sid = "22222222-2222-2222-2222-222222222222"
    legacy_payload = {
        "format": 2, "session_id": sid,
        "created_at": datetime.now().astimezone().isoformat(),
        "expires_at": (datetime.now().astimezone() + timedelta(hours=1)).isoformat(),
        "entities": [{
            "token": "[PERSON_1]", "entity_type": "PERSON", "original_text": "Иванов",
            "segment_id": "p0", "canonical": "Иванов",
            "occurrences": [{"segment_id": "p0", "start": 0, "end": 6, "spans": None,
                              "surface": "Иванов"}],
        }],
    }
    enc = Fernet(key).encrypt(json.dumps(legacy_payload, ensure_ascii=False).encode("utf-8"))
    (sessions_dir / f"{sid}.enc").write_bytes(enc)

    data = storage.load_session(sid)  # не должно упасть
    rec = data["entities"][0]
    assert rec.get("detector") is None
    assert rec["occurrences"][0].get("detector") is None
