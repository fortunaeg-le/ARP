"""Блок 3 — NER-детектор.

Находит PERSON/ORG через стандартный NER-пайплайн Natasha и почтовые адреса
(ADDRESS) через natasha.AddrExtractor. Публичная функция — detect_ner.

Модели Natasha инициализируются один раз при импорте модуля (это секунды и
сотни МБ памяти — см. HANDOFF_3, раздел "Побочные эффекты импорта").
"""

import re
import sys
import uuid

import yaml

from models import Entity, SourceDocument
from natasha import (
    Segmenter,
    MorphVocab,
    NewsEmbedding,
    NewsNERTagger,
    AddrExtractor,
    Doc,
)

# --- Инициализация моделей один раз при старте модуля ---
_segmenter = Segmenter()
_morph_vocab = MorphVocab()
_emb = NewsEmbedding()
_ner_tagger = NewsNERTagger(_emb)
_addr_extractor = AddrExtractor(_morph_vocab)

# Расширение PERSON-спана инициалами вида "И.И." справа/слева от спана.
# Паттерны из ТЗ применяются к срезам text[end:] / text[:start], поэтому якоря
# ^ и $ работают на границе среза (а не на реальном начале/конце сегмента).
_INITIALS_RIGHT = re.compile(r"^[  ]?[А-ЯЁ]\.[  ]?[А-ЯЁ]\.")
_INITIALS_LEFT = re.compile(r"[А-ЯЁ]\.[  ]?[А-ЯЁ]\.[  ]?$")

# Символы, из которых (и только из которых) может состоять разрыв между
# двумя соседними Match'ами AddrExtractor, чтобы их склеить в один Entity.
_ADDR_GAP_CHARS = frozenset(" ,. ")
_ADDR_GAP_MAXLEN = 6

# --- Гибридная детекция адреса (этап A волны 2): место — контекстно, спан — полный ---
#
# Раньше AddrExtractor (yargy) гонялся СКАНЕРОМ по тексту КАЖДОГО сегмента (~95%
# времени детекции, см. scratch/RECON_REPORT.md) и всё равно ронял хвост адреса
# (дом/индекс) в ~2/3 случаев. Теперь yargy — ПАРСЕР окрестности, а не сканер:
#   1. Место адреса детектируем контекстно: LOC-спаны NER-тэггера (бессловарно) +
#      regex-триггер на сильные адресные маркеры — для адресов с НЕЗНАКОМЫМ топонимом,
#      которые LOC не знает («д. Простоквашино»). Маркеры — ДОПОЛНЕНИЕ к LOC (объединение),
#      а НЕ единственный механизм: сам адрес подтверждают LOC и/или yargy, маркер лишь
#      решает «здесь есть место → стоит запустить парсер».
#   2. yargy запускаем ТОЛЬКО на сегментах с хитом. Сложность O(весь текст)→O(хиты).
#   3. Итоговый спан = объединение LOC- и yargy-спанов, кластеризованное по близости
#      (склеивает разорванные грамматикой куски вроде «…наб. реки Фонтанки…») и
#      КОНСЕРВАТИВНО расширенное по краям адресными токенами (индекс/тип-улицы/дом/кв),
#      НИКОГДА не сужаемое. Асимметрия цены: лишнее закрытое слово — шум, незакрытый
#      хвост адреса — утечка ПДн; при сомнении закрываем БОЛЬШЕ.

# Сильные адресные маркеры МЕСТА — ТРИГГЕР запуска yargy на сегменте (в дополнение к LOC).
# Намеренно БЕЗ одиночных дом/кв («д. 5», «кв. 12»): они частотны в юр-ссылках и
# анкорили бы парсер на неадресных сегментах. Намеренно БЕЗ голого почтового индекса
# (\\b\\d{6}\\b): в корпусе он ВСЕГДА соседствует с городом (то есть уже покрыт LOC/
# маркером места), а как самостоятельный анкор ловил паспортный хвост «…паспорт 4509
# 123456» и через yargy-ошибку на ФИО давал ложный ADDRESS. Анкор — это МЕСТО
# (город/тип-поселения/улица/регион); «д.\\s*<буква>» ловит деревню-топоним, не «д. <цифра>».
_ADDR_ANCHOR_RE = re.compile(
    r"(?i)("
    r"\bг\.\s*[а-яё]|\bгор\.\s*[а-яё]|\bгород\s+[а-яё]"              # город
    r"|\bд\.\s*[а-яё]|\b(?:пос|пгт|рп|дер|деревня|село|ст-ца|станица|хутор|мкр|мкр-н|мкрн)\b"  # поселение
    r"|\b(?:обл\.|область|край|респ\.|республика|р-н|район|округ)\b"  # регион
    r"|\b(?:ул\.|улица|пр-т|пр-кт|проспект|пер\.|переулок|наб\.|набережная"
    r"|ш\.|шоссе|б-р|бульвар|проезд|линия|тупик|аллея|пл\.|площадь|квартал)\b"  # улица
    r")"
)

# Кластеризация: два адресных куска склеиваются в один, если разрыв между ними не
# длиннее — закрывает многословные названия улиц, которые грамматика yargy рвёт на
# части («г. СПб» ‖ «наб. реки Фонтанки» ‖ «д. 15»).
_ADDR_CLUSTER_GAP = 35
# Предел консервативного расширения края в ОДНУ сторону. Типовой хвост адреса
# («, д. 5, кв. 12, стр. 1» / «, помещение 4») короче — предел не даёт расширению
# убежать в прозу, если рядом почему-то не нашлось стоп-слова.
_ADDR_EXPAND_MAX = 48

# Адресные слова для КЛАССИФИКАЦИИ токена при расширении края (не для детекции места).
# Точки в конце снимаются перед проверкой; дефисные формы («р-н», «пр-т») хранятся как есть.
_ADDR_MARKER_WORDS = frozenset(
    "г гор город пос п пгт рп с д дер деревня село мкр мкрн мкр-н обл область край респ "
    "республика р-н район ао округ ст станица х хутор "
    "ул улица пр пр-т пр-кт пркт проспект пер переулок наб набережная ш шоссе б-р бульвар "
    "проезд линия тупик аллея пл площадь квартал тракт "
    "дом корп корпус к стр строение кв квартира оф офис пом помещение лит литера влд "
    "владение зд здание "
    "км рф россия федерация а/я".split()
)

# Один «токен» при расширении — максимальный кусок без пробелов и запятых.
_ADDR_TOKEN_FWD_RE = re.compile(r"[\s,]*([^\s,]+)")
_ADDR_TOKEN_RE = re.compile(r"[^\s,]+")


def _classify_addr_token(tok: str) -> str:
    """Классифицирует токен на границе адреса: 'addr' (часть адреса — продолжать
    расширение), 'skip' (пустой/пунктуация — пройти, но не расширять), 'stop'
    (незнакомое слово в нижнем регистре — мы вышли из адреса)."""
    core = tok.strip(" \t.,;:()«»\"'")
    if not core:
        return "skip"
    if any(ch.isdigit() for ch in core):
        return "addr"                         # индекс/дом/«22-й»/«132/3»/«6А»
    low = core.lower().rstrip(".")
    if low in _ADDR_MARKER_WORDS:
        return "addr"                         # адресный маркер (тип улицы/дома/поселения)
    if core[0].isupper():
        return "addr"                         # топоним / название улицы (имя собственное)
    return "stop"                             # слово нижнего регистра, не маркер — конец адреса


def _expand_addr_right(text: str, end: int) -> int:
    """Тянет правый край адреса по адресным токенам, НИКОГДА не сужая.
    Останавливается на первом не-адресном слове или на пределе _ADDR_EXPAND_MAX."""
    p = end
    limit = min(len(text), end + _ADDR_EXPAND_MAX)
    while p < len(text):
        m = _ADDR_TOKEN_FWD_RE.match(text, p)
        if m is None or m.start(1) >= limit:
            break
        cls = _classify_addr_token(m.group(1))
        if cls == "stop":
            break
        p = m.end(1)
        if cls == "addr":
            end = p
    return end


# Левое расширение УЖЕ правого: тянем влево только ведущий индекс/регион/страну
# (то, что грамматика yargy роняет слева: «Российская Федерация, 101000, …»). НЕ
# грабим произвольное слово с заглавной буквы — иначе съедаем метку ячейки («Адрес:»,
# «Юридический адрес:») в токен, теряя контекст для LLM. Асимметрия слева слабее:
# страна/регион — наименее идентифицирующая часть, её недозакрытие не утечка дома/кв.
_ADDR_LEFT_WORDS = frozenset(
    "россия российская российской российскую федерация федерации рф".split()
)


def _classify_addr_left_token(tok: str) -> str:
    """Классификатор ЛЕВОГО расширения (уже правого: левый край не должен съедать
    метку ячейки «Адрес:» в токен). Три исхода:
      'commit' — цифра (индекс) / адресный маркер / форма страны: фиксируем сюда;
      'pass'   — пустой ИЛИ слово с ЗАГЛАВНОЙ (метка/регион-прилагательное): проходим
                 сквозь, но не фиксируем — впитается, лишь если левее есть 'commit';
      'stop'   — слово нижнего регистра не-маркер: вышли из адреса влево."""
    core = tok.strip(" \t.,;:()«»\"'")
    if not core:
        return "pass"
    if any(ch.isdigit() for ch in core):
        return "commit"
    low = core.lower().rstrip(".")
    if low in _ADDR_MARKER_WORDS or low in _ADDR_LEFT_WORDS:
        return "commit"
    if core[0].isupper():
        return "pass"
    return "stop"


def _expand_addr_left(text: str, start: int) -> int:
    """Тянет левый край адреса влево по индексу/региону/маркерам, НИКОГДА не сужая.
    Заглавное слово впитывается, лишь если левее нашёлся фиксируемый адресный токен —
    так «обл. Московская,»/«Российская Федерация,» входят, а метка «Адрес:» нет."""
    bound = max(0, start - _ADDR_EXPAND_MAX)
    prefix = text[bound:start]
    new_start = start
    for m in reversed(list(_ADDR_TOKEN_RE.finditer(prefix))):
        cls = _classify_addr_left_token(m.group(0))
        if cls == "stop":
            break
        if cls == "commit":
            new_start = bound + m.start()
        # 'pass': идём влево, не фиксируя (впитается ретроспективно при commit)
    return new_start


def _filter_suspect_yargy(
    text: str,
    yargy_spans: list[tuple[int, int]],
    ner_spans: list[tuple[int, int]],
    loc_spans: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Отбрасывает yargy-спаны, похожие на ложное срабатывание на ФИО/названии: они
    ПЕРЕКРЫВАЮТ уже найденную PER/ORG-сущность и при этом НЕ подкреплены ни LOC, ни
    адресным маркером внутри себя. yargy иногда принимает последовательность заглавных
    (имя) за топоним; такой обрывок, склеенный кластеризацией с настоящим адресом рядом,
    затянул бы ФИО и метку («адрес:») в ADDRESS-токен (человек мислейбелится адресом,
    своего PERSON-токена не получает). LOC/маркеры/расширение восстанавливают настоящий
    адрес и без этого спана, поэтому его снятие безопасно для полноты (проверено GOLDEN)."""
    if not ner_spans:
        return yargy_spans
    kept: list[tuple[int, int]] = []
    for s, e in yargy_spans:
        overlaps_ner = any(max(s, ns) < min(e, ne) for ns, ne in ner_spans)
        overlaps_loc = any(max(s, ls) < min(e, le) for ls, le in loc_spans)
        has_marker = _ADDR_ANCHOR_RE.search(text[s:e]) is not None
        if overlaps_ner and not overlaps_loc and not has_marker:
            continue
        kept.append((s, e))
    return kept


def _build_address_spans(text: str, raw_spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Собирает финальные адресные спаны из «сырых» хитов (LOC ∪ yargy):
    кластеризует по близости, расширяет края по адресным токенам, повторно
    склеивает пересечения, возникшие от расширения. Полнота спана важнее точности
    границ (см. асимметрию цены)."""
    spans = sorted({(s, e) for s, e in raw_spans if 0 <= s < e})
    if not spans:
        return []

    clusters: list[tuple[int, int]] = []
    cs, ce = spans[0]
    for s, e in spans[1:]:
        if s - ce <= _ADDR_CLUSTER_GAP:
            ce = max(ce, e)
        else:
            clusters.append((cs, ce))
            cs, ce = s, e
    clusters.append((cs, ce))

    expanded = [
        (_expand_addr_left(text, s), _expand_addr_right(text, e))
        for s, e in clusters
    ]

    expanded.sort()
    merged = [expanded[0]]
    for s, e in expanded[1:]:
        ps, pe = merged[-1]
        if s <= pe:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def _load_ner_config(config_path: str) -> tuple[dict, list[str]]:
    """Читает entity_types.yaml и возвращает:
      - ner_label_map: {метка Natasha -> entity_type} для записей method: ner с ner_label
        (напр. {"PER": "PERSON", "ORG": "ORG"});
      - addr_types: список entity_type для записей method: ner с ner_extractor: addr
        (обычно ["ADDRESS"]).
    Записи с enabled: false пропускаются. Файл обязан существовать — иначе FileNotFoundError.
    """
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    ner_label_map: dict[str, str] = {}
    addr_types: list[str] = []

    for entity_type, spec in config["entity_types"].items():
        if not isinstance(spec, dict) or spec.get("method") != "ner":
            continue
        if spec.get("enabled", True) is False:
            continue

        ner_label = spec.get("ner_label")
        ner_extractor = spec.get("ner_extractor")

        if ner_extractor == "addr":
            addr_types.append(entity_type)
        elif ner_label is not None:
            ner_label_map[ner_label] = entity_type
        else:
            print(
                f"ПРЕДУПРЕЖДЕНИЕ: тип {entity_type} (method: ner) без ner_label и без "
                f"ner_extractor: addr — пропущен",
                file=sys.stderr,
            )

    return ner_label_map, addr_types


def _expand_person_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Расширяет спан PERSON инициалами "И.И." в обе стороны независимо.
    Возвращает новые (start, end)."""
    right = _INITIALS_RIGHT.match(text[end:])
    if right is not None:
        end = end + right.end()

    left = _INITIALS_LEFT.search(text[:start])
    if left is not None:
        start = left.start()

    return start, end


def _glue_address_matches(text: str, matches: list) -> list[tuple[int, int]]:
    """Склеивает соседние Match'и AddrExtractor в цепочки-адреса.
    Соседние склеиваются, если разрыв между match_i.stop и match_{i+1}.start
    не длиннее 6 символов и целиком состоит из [ ,.\\u00A0] (без букв).
    Возвращает список (start, end) итоговых спанов."""
    if not matches:
        return []

    ordered = sorted(matches, key=lambda m: (m.start, m.stop))

    spans: list[tuple[int, int]] = []
    cur_start = ordered[0].start
    cur_end = ordered[0].stop

    for m in ordered[1:]:
        gap = text[cur_end:m.start]
        glue = (
            0 <= len(gap) <= _ADDR_GAP_MAXLEN
            and all(ch in _ADDR_GAP_CHARS for ch in gap)
        )
        if glue:
            cur_end = m.stop
        else:
            spans.append((cur_start, cur_end))
            cur_start = m.start
            cur_end = m.stop

    spans.append((cur_start, cur_end))
    return spans


def detect_ner(doc: SourceDocument, config_path: str) -> list[Entity]:
    ner_label_map, addr_types = _load_ner_config(config_path)

    entities: list[Entity] = []

    for segment in doc.segments:
        orig_text = segment.text
        if not orig_text:
            continue

        # Весь путь детекции (Doc, расширение инициалов, AddrExtractor, склейка окон)
        # работает с detection_text — копией той же длины с нормализованным регистром,
        # если сегмент был визуально заглавным/строчным. original_text у каждого Entity
        # вырезается из НАСТОЯЩЕГО segment.text (orig_text) по тем же оффсетам: равная
        # длина делает их валидными в обоих. Для сегментов без detection_text это ровно
        # segment.text — поведение идентично прежнему.
        text = segment.metadata.get("detection_text", orig_text)

        # --- NER-тэггер Natasha: PERSON/ORG (по конфигу) + LOC-спаны (для адреса) ---
        # Тэггер нужен и для адреса (LOC — контекстный детектор места), поэтому
        # запускаем его, если сконфигурирован хоть PER/ORG, хоть ADDRESS.
        loc_spans: list[tuple[int, int]] = []
        ner_spans: list[tuple[int, int]] = []   # PER/ORG-спаны для отсева yargy-ФИО-ложняков
        if ner_label_map or addr_types:
            nlp_doc = Doc(text)
            nlp_doc.segment(_segmenter)
            nlp_doc.tag_ner(_ner_tagger)
            for span in nlp_doc.spans:
                if span.type == "LOC":
                    loc_spans.append((span.start, span.stop))
                    continue
                entity_type = ner_label_map.get(span.type)
                if entity_type is None:
                    continue  # метка Natasha не сопоставлена ни одному типу конфига

                start, end = span.start, span.stop
                if span.type == "PER":
                    start, end = _expand_person_span(text, start, end)
                ner_spans.append((start, end))

                entities.append(Entity(
                    id=str(uuid.uuid4()),
                    segment_id=segment.id,
                    start=start,
                    end=end,
                    original_text=orig_text[start:end],
                    entity_type=entity_type,
                    detector="ner",
                    confidence=1.0,
                ))

        # --- ADDRESS: гибрид «LOC/маркеры детектируют место → yargy-парсер добирает
        # полный спан → консервативное расширение закрывает хвост». yargy НЕ гоняется
        # по сегментам без хита (главный источник ускорения этапа A). ---
        if addr_types:
            has_marker = _ADDR_ANCHOR_RE.search(text) is not None
            if loc_spans or has_marker:
                # yargy — только здесь (окрестность хита), не по всему тексту
                yargy_spans = _glue_address_matches(text, list(_addr_extractor(text)))
                # Отсев yargy-ложняков на ФИО/названиях (перекрывают PER/ORG без LOC/маркера):
                # иначе кластеризация затянула бы имя и метку в ADDRESS-токен.
                yargy_spans = _filter_suspect_yargy(text, yargy_spans, ner_spans, loc_spans)
                # Сам адрес подтверждают LOC и/или yargy (маркер — лишь триггер запуска):
                # маркерные позиции в «сырьё» НЕ кладём, иначе список маркеров стал бы
                # самостоятельным детектором. Расширение краёв использует маркеры как
                # линейку, но стартует только от подтверждённых LOC/yargy-спанов.
                for start, end in _build_address_spans(text, loc_spans + yargy_spans):
                    for addr_type in addr_types:
                        entities.append(Entity(
                            id=str(uuid.uuid4()),
                            segment_id=segment.id,
                            start=start,
                            end=end,
                            original_text=orig_text[start:end],
                            entity_type=addr_type,
                            detector="ner",
                            confidence=1.0,
                        ))

    # Инвариант блока 3: original_text строго равен срезу сегмента по [start:end].
    for e in entities:
        seg = next(s for s in doc.segments if s.id == e.segment_id)
        actual = seg.text[e.start:e.end]
        if e.original_text != actual:
            raise AssertionError(
                f"Нарушен инвариант original_text для {e.entity_type} в {e.segment_id} "
                f"[{e.start}:{e.end}]: ожидалось {e.original_text!r}, получено {actual!r}"
            )

    return entities
