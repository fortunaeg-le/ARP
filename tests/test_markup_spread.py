"""ЭТАП MARKUP-SPREAD — распространение ручной разметки по документу.

Два слоя проверок:
  * единичные — сам примитив `src/markup_spread.py` (морфология, буквальный
    режим, ЛОВУШКИ пункта 3 постановки: омоним-нарицательное, топоним против
    фирмы, частичное совпадение);
  * живой — настоящий `app/server.py` в отдельном процессе: пометить значение,
    встречающееся в документе несколько раз в разных падежах, применить, увидеть
    все маски и собрать текст обратно СИМВОЛ В СИМВОЛ.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_ROOT, "app")
sys.path.insert(0, os.path.join(_ROOT, "src"))

from detokenizer import detokenize                 # noqa: E402
from markup_spread import find_occurrences          # noqa: E402
from models import SourceDocument, TextSegment      # noqa: E402


def _doc(*lines):
    """Документ из строк — та же сегментация, что у экстрактора для .txt."""
    return SourceDocument(
        segments=[TextSegment(id="l%d" % i, text=t, source_type="txt_line",
                              metadata={"line_index": i})
                  for i, t in enumerate(lines)],
        source_format="txt", source_path="probe.txt")


def _find(doc, seg_id, needle, entity_type, occupied=None):
    seg = next(s for s in doc.segments if s.id == seg_id)
    start = seg.text.index(needle)
    return find_occurrences(doc, occupied or {}, seg_id, start,
                            start + len(needle), entity_type)


def _surfaces(found):
    return [f["surface"] for f in found]


# --------------------------------------------------------------------------- #
#                       Задача 2 — падежи и все узлы документа                 #
# --------------------------------------------------------------------------- #

def test_surname_spreads_across_cases():
    """Пометили «Коваль» — закрылись «Коваля», «Ковалю», «Ковалём». Фамилия
    взята намеренно такая, которой pymorphy НЕ даёт помету Surn: ручная пометка
    обязана работать и там, где морфология молчит о роли слова."""
    doc = _doc("Исполнитель — Коваль, именуемый в дальнейшем «Работник».",
                "Заказчик передаёт Ковалю материалы.",
                "Подпись Коваля заверена.",
                "Договор подписан Ковалём лично.")
    found = _find(doc, "l0", "Коваль", "PERSON")
    assert _surfaces(found) == ["Ковалю", "Коваля", "Ковалём"], found
    assert {f["segment_id"] for f in found} == {"l1", "l2", "l3"}
    # координаты — реальные срезы своих сегментов
    for f in found:
        seg = next(s for s in doc.segments if s.id == f["segment_id"])
        assert seg.text[f["start"]:f["end"]] == f["surface"]


def test_spread_reaches_table_cells_and_headers():
    """Колонтитул и ячейка таблицы — такие же сегменты; распространение обязано
    доставать до них, иначе «закрыто» опять окажется неправдой."""
    doc = SourceDocument(
        segments=[
            TextSegment(id="p0", text="Работник: Коваль Пётр.",
                        source_type="docx_paragraph", metadata={"paragraph_index": 0}),
            TextSegment(id="h1", text="Личное дело Коваля",
                        source_type="docx_paragraph", metadata={"part": "header1"}),
            TextSegment(id="t0_r1_c0", text="Ковалю",
                        source_type="docx_table_cell",
                        metadata={"table_index": 0, "row_index": 1, "col_index": 0}),
        ],
        source_format="docx", source_path="probe.docx")
    found = _find(doc, "p0", "Коваль", "PERSON")
    assert {f["segment_id"] for f in found} == {"h1", "t0_r1_c0"}


def test_occupied_spans_are_not_touched():
    """Там, где маска уже стоит, распространение не лезет."""
    doc = _doc("Исполнитель — Коваль.", "Передано Ковалю.")
    occupied = {"l1": [(9, 15)]}          # «Ковалю» уже закрыт
    assert _find(doc, "l0", "Коваль", "PERSON", occupied) == []


def test_literal_types_do_not_use_morphology():
    """Реквизит распространяется буквально и только на границах слова: кусок
    длинного числа маской не становится."""
    doc = _doc("ИНН 7755615081 указан в шапке.",
                "Реквизиты: ИНН 7755615081.",
                "Служебный код 77556150819999.")
    found = _find(doc, "l0", "7755615081", "INN")
    assert [f["segment_id"] for f in found] == ["l1"]


# --------------------------------------------------------------------------- #
#         Задача 3 — ЛОВУШКИ: то же слово в другом месте значит другое         #
# --------------------------------------------------------------------------- #

def test_trap_common_noun_homonym_is_not_masked():
    """«Мороз» человек и «мороз» погода. Строчная буква — признак нарицательного;
    вхождение со строчной не берётся."""
    doc = _doc("Гражданин Мороз Иван Петрович, паспорт 45 11 123456.",
                "Работы приостанавливаются, если ударил мороз ниже -25 C.",
                "Претензия вручена Морозу лично.")
    found = _find(doc, "l0", "Мороз", "PERSON")
    assert _surfaces(found) == ["Морозу"], found
    assert all(f["segment_id"] != "l1" for f in found)


def test_trap_toponym_is_not_masked_as_person_or_org():
    """«Самара» в адресе и «Самара» в названии фирмы — разные сущности.
    Кандидат не имеет права ПРИОБРЕСТИ жанровый признак, которого у якоря не
    было: помеченное в кавычках имя фирмы не закрывает город в адресе, и
    наоборот."""
    doc = _doc("Поставщик — ООО «Самара», ИНН 7755615081.",
                "Адрес склада: г. Самара, ул. Ленина, д. 5.",
                "Договор с Самарой подписан.")
    found = _find(doc, "l0", "Самара", "ORG")
    # город в адресе (адресный тип-маркер слева) — не закрыт
    assert all(f["segment_id"] != "l1" for f in found), found
    # а та же фирма в прозе — закрыта: предлог «с» не адресный маркер «с.» (село),
    # иначе законное вхождение осталось бы открытым
    assert [f["surface"] for f in found] == ["Самарой"], found
    # обратное направление: пометили город — фирма в кавычках не закрывается
    back = _find(doc, "l1", "Самара", "ADDRESS")
    assert all(f["segment_id"] != "l0" for f in back), back


def test_trap_partial_match_is_not_masked():
    """Пометили «Иванов» — «Ивановский» НЕ закрывается: сопоставление идёт по
    целым токенам и нормальным формам, лемма «ивановский» ключу «иванов» не
    равна."""
    doc = _doc("Гражданин Иванов Сергей Петрович.",
                "Объект расположен в Ивановском районе.",
                "Ивановская область, г. Кинешма.",
                "Претензия вручена Иванову.")
    found = _find(doc, "l0", "Иванов", "PERSON")
    assert _surfaces(found) == ["Иванову"], found


def test_very_short_key_does_not_spread():
    """Однословный ключ короче трёх букв не распространяется вовсе."""
    doc = _doc("Сторона Ли подписала.", "Ли и партнёры.", "ли бы да кабы")
    assert _find(doc, "l0", "Ли", "PERSON") == []


def test_multiword_lowercase_still_spreads():
    """Проверка регистра — только на ОДНОСЛОВНОМ ключе: многословное совпадение
    само по себе есть форма ФИО и омонимом нарицательного быть не может."""
    doc = _doc("Кузнецова Мария Дмитриевна, паспорт 45 11 123456.",
                "подписано кузнецовой марией дмитриевной")
    found = _find(doc, "l0", "Кузнецова Мария Дмитриевна", "PERSON")
    assert _surfaces(found) == ["кузнецовой марией дмитриевной"], found


def test_year_marker_is_not_read_as_city():
    """«…2025 г.» — это ГОД, а не город: маркер «г.» здесь не должен запрещать
    распространение (замер по корпусу v1 дал из-за этого 63 ложных отказа)."""
    doc = _doc("г. Самара     «07» мая 2025 г.",
                "ПАО «Альтаир» в лице директора.",
                "Реквизиты: ПАО «Альтаир», ИНН 7755615081.")
    found = _find(doc, "l1", "ПАО «Альтаир»", "ORG")
    assert [f["segment_id"] for f in found] == ["l2"], found
    # закрывающая кавычка не теряется — иначе в тексте осталась бы висячая «»»
    assert _surfaces(found) == ["ПАО «Альтаир»"]


# --------------------------------------------------------------------------- #
#                     Живой круг: интерфейс + восстановление                   #
# --------------------------------------------------------------------------- #

def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_server(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    port = _free_port()
    env = dict(os.environ, USERPROFILE=str(home), HOME=str(home),
               SHIFRATOR_UI_PORT=str(port))
    proc = subprocess.Popen(
        [sys.executable, os.path.join(_APP, "server.py")], env=env, cwd=_ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = "http://127.0.0.1:%d" % port
    try:
        ok = False
        for _ in range(100):
            try:
                with urllib.request.urlopen(base + "/api/ping", timeout=0.5) as r:
                    json.loads(r.read().decode("utf-8"))
                    ok = True
                    break
            except Exception:
                time.sleep(0.1)
        assert ok, "сервер не поднялся вовремя"
        yield base, home
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _req(base, method, path, body=None, headers=None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        else:
            data = body
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _encrypt_sync(base, text, filename="doc.txt"):
    started = _req(base, "POST", "/api/encrypt/start", body=text.encode("utf-8"),
                    headers={"X-Filename": filename, "X-Allow-Lossy": "0",
                             "Content-Type": "application/octet-stream"})
    assert started["status"] == "ok", started
    job_id = started["job_id"]
    for _ in range(300):
        st = _req(base, "GET", "/api/encrypt/status?job_id=%s" % job_id)
        assert st["status"] != "error", st
        if st["status"] == "done":
            return st["result"]
        time.sleep(0.1)
    raise AssertionError("encrypt job не завершился вовремя")


DOC = (
    "ДОГОВОР ОКАЗАНИЯ УСЛУГ\n"
    "Исполнитель — Коваль, именуемый в дальнейшем «Работник».\n"
    "Заказчик передаёт Ковалю материалы по акту.\n"
    "Подпись Коваля заверена печатью.\n"
    "Договор подписан Ковалём лично.\n"
)


def test_manual_markup_closes_all_occurrences_and_restores(live_server):
    """Приёмка нажатием: значение встречается 4 раза в разных падежах; человек
    помечает ОДНО вхождение — закрываются все, а текст собирается обратно
    символ в символ."""
    base, home = live_server
    res = _encrypt_sync(base, DOC)
    sid = res["session_id"]
    lines = DOC.split("\n")[:-1]
    assert "Коваль," in lines[1]
    start = lines[1].index("Коваль")
    marked = _req(base, "POST", "/api/markup/mark-missed",
                   body={"session_id": sid, "segment_id": "l1",
                         "start": start, "end": start + len("Коваль"),
                         "entity_type": "PERSON"})
    assert marked["status"] == "ok", marked

    applied = _req(base, "POST", "/api/markup/apply", body={"session_id": sid})
    assert applied["status"] == "ok", applied
    # задача 4: интерфейсу отдано и число, и куда именно встали маски
    assert applied["spread_total"] == 3, applied["spread_total"]
    entry = applied["results"][0]
    assert entry["spread_count"] == 3
    # порядок — документа, сверху вниз, чтобы человек читал «где закрылось» подряд
    assert [x["surface"] for x in entry["spread"]] == ["Ковалю", "Коваля", "Ковалём"]
    assert [x["segment_id"] for x in entry["spread"]] == ["l2", "l3", "l4"]

    anon = applied["anon_text"]
    for form in ("Коваль", "Ковалю", "Коваля", "Ковалём"):
        assert form not in anon, "открытым осталось %r:\n%s" % (form, anon)

    # ВОССТАНОВЛЕНИЕ ПОСЛЕ РАСПРОСТРАНЕНИЯ, режим `exact` (тот, которым
    # собирается принесённый обратно файл): каждое из четырёх вхождений обязано
    # вернуться в СВОЁМ падеже, а не одной формой якоря на все четыре места —
    # то есть текст собирается символ в символ.
    restored, unresolved = detokenize(anon, sid,
                                       storage_dir=str(home / ".shifrator" / "sessions"),
                                       mode="exact")
    assert unresolved == [], unresolved
    assert restored == "\n".join(lines), restored

    # Путь ответа LLM (режим `canonical`) распространение не ломает: токен
    # разрешается везде, непонятых токенов нет.
    back = _req(base, "POST", "/api/decrypt", body={"session_id": sid, "text": anon})
    assert back["unresolved"] == [], back["unresolved"]
    assert back["restored"].count("Коваль") == 4, back["restored"]
