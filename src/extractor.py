import re
import sys
import zipfile
from pathlib import Path

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.hyperlink import Hyperlink
from docx.oxml.ns import qn

from models import SourceDocument, TextSegment


# --- Нормализация регистра для detection_text (см. docs/SHIFRATOR_SPEC_AI.md, блок 1) ---
#
# Natasha опирается на регистр как признак имени собственного. Если текст .docx
# визуально заглавный через ФОРМАТИРОВАНИЕ (run.font.all_caps/small_caps), в XML
# физически лежат строчные буквы, python-docx их и возвращает — и ФИО в преамбуле
# договора остаётся в открытом виде. Рядом с настоящим segment.text заводим
# detection_text: копию ТОЙ ЖЕ ДЛИНЫ с нормализованным регистром. Детекторы ищут в
# detection_text, а original_text вырезают из настоящего text по тем же оффсетам —
# равная длина делает оффсеты валидными в обоих. Инвариант равной длины —
# фундамент схемы; при его нарушении detection_text НЕ создаётся вообще.

# Порог длины сегмента для эвристики Изменения 2 (настоящий строчный/заглавный ввод
# без форматирования). Узко — чтобы эвристика не превратилась во второй полный проход
# NER по всему документу.
_MAX_HEURISTIC_LEN = 200

# Признак пункта перечисления: "(1)" или "1." после необязательных ведущих пробелов.
# Эвристика Изменения 2 срабатывает ТОЛЬКО на таких сегментах (см. раздел про
# известную непокрытую дыру в отчёте: ФИО строчными в обычном абзаце БЕЗ нумерации
# найдено НЕ будет).
_LIST_ITEM_RE = re.compile(r"^\s*(\(\d+\)|\d+\.)")


def _normalize_word(word: str) -> str:
    """Нормализует регистр ОДНОГО слова (максимального alpha-рана) посимвольно.

    НЕ используем str.title(): он (1) не гарантирует сохранение длины на лигатурах
    ('ﬁ' -> 'Fi') — а равная длина есть фундамент всей схемы; (2) ломает смысл —
    'ип пирогова' -> 'Ип Пирогова', разрушая юр-маркер 'ИП', на котором строится
    синтаксический слой составных сущностей ('ИП + ФИО') из следующей задачи.

    Вместо этого: слово длиной 2-3 буквы целиком поднимаем в верхний регистр —
    так 'ип'->'ИП', 'ооо'->'ООО', 'ао'->'АО', 'зао'->'ЗАО', 'пао'->'ПАО',
    'кфх'->'КФХ' переживают нормализацию all_caps-рана как юр-формы, а не как
    'Ип'/'Ооо'. Это часть посимвольной логики (правило по длине), а не список
    исключений: любое короткое (2-3) полностью буквенное слово в визуально
    заглавном контексте логично оставить заглавным. Остальные слова: первая буква
    в верхний, прочие в нижний — но ТОЛЬКО через .upper()/.lower() над отдельными
    символами (не над строкой целиком). Инициалы ('а' в 'а.с.') — слово длины 1,
    первая буква -> 'А'."""
    if len(word) in (2, 3):
        return "".join(ch.upper() for ch in word)
    return "".join(
        ch.upper() if idx == 0 else ch.lower()
        for idx, ch in enumerate(word)
    )


def _normalize_case(text: str) -> str:
    """Нормализует регистр строки, разбивая её на слова (максимальные alpha-раны)
    посимвольно. Не-буквенные символы (пробелы, пунктуация, цифры, \\t, \\n)
    копируются как есть и служат границами слов. Длина МОЖЕТ измениться только на
    редких символах, где ch.upper()/ch.lower() дают не один символ — это ловит
    хард-гейт длины на уровне сегмента у вызывающего кода."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isalpha():
            j = i
            while j < n and text[j].isalpha():
                j += 1
            out.append(_normalize_word(text[i:j]))
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


# --- Разрешение эффективного all_caps / small_caps по цепочке наследования OOXML ---
#
# В OOXML all_caps/small_caps трёхзначны: True / False / None(«наследуй от родителя»).
# run.font.all_caps отражает ТОЛЬКО прямое форматирование рана (w:rPr/w:caps); капс,
# заданный СТИЛЕМ (частый в реальных договорах способ для шапок/преамбулы «СТОРОНЫ»),
# там None. Разрешение идёт по приоритету (высший → низший): прямой ран → стиль рана
# (character style) → стиль абзаца → базовые стили рекурсивно → стиль таблицы (для
# ячеек) → docDefaults. Первое НЕ-None значение выигрывает; явный False на любом
# уровне ОТМЕНЯЕТ унаследованный сверху True (не схлопываем трёхзначность в двузначность).
# Эмпирика уровней — см. отчёт по дофиксу наследования стиля.

_CAPS_TAG = {"all_caps": "w:caps", "small_caps": "w:smallCaps"}

# Сентинел «в кэше ничего нет» — отличает отсутствие ключа от валидного None
# (None здесь означает «наследуй», а не «не вычисляли»).
_MISSING = object()


def _onoff(element) -> bool | None:
    """Трёхзначное значение w:caps/w:smallCaps: None (элемента нет → наследуй),
    иначе булево по w:val (атрибут отсутствует → True; иначе '1'/'true'/'on' → True,
    '0'/'false'/'off' → False) — та же семантика, что у python-docx ST_OnOff."""
    if element is None:
        return None
    val = element.get(qn("w:val"))
    if val is None:
        return True
    return val in ("1", "true", "on")


def _read_docdefaults_caps(document, attr: str) -> bool | None:
    """docDefaults документа: w:docDefaults/w:rPrDefault/w:rPr/{w:caps|w:smallCaps}.
    python-docx это не выставляет через font-API, поэтому читаем XML напрямую (lxml
    уже в зависимостях). Возвращает True/False/None."""
    try:
        styles_el = document.styles.element
        dd = styles_el.find(qn("w:docDefaults"))
        if dd is None:
            return None
        rpr_default = dd.find(qn("w:rPrDefault"))
        if rpr_default is None:
            return None
        rpr = rpr_default.find(qn("w:rPr"))
        if rpr is None:
            return None
        return _onoff(rpr.find(qn(_CAPS_TAG[attr])))
    except Exception:
        # Битый/нестандартный styles.xml не должен ронять извлечение.
        return None


class _CapsResolver:
    """Разрешает эффективные all_caps/small_caps рана с учётом наследования стилей.

    Кэширует результат прохода по цепочке base_style ПО ОБЪЕКТУ СТИЛЯ (стилей в
    документе десятки, ранов — тысячи): без кэша каждый ран заново шёл бы вверх по
    цепочке. Защита от циклов (битые .docx с циклическим base_style существуют) —
    множество уже посещённых элементов стиля в пределах одного прохода."""

    def __init__(self, document) -> None:
        self._chain_cache: dict[tuple[int, str], bool | None] = {}
        self._docdefaults = {
            attr: _read_docdefaults_caps(document, attr)
            for attr in _CAPS_TAG
        }

    def _style_chain(self, style, attr: str) -> bool | None:
        """Первое НЕ-None значение attr вдоль цепочки base_style (сам стиль → его
        база → ...), с защитой от циклов и мемоизацией по стартовому стилю."""
        if style is None:
            return None
        key = (id(style._element), attr)
        cached = self._chain_cache.get(key, _MISSING)
        if cached is not _MISSING:
            return cached

        result: bool | None = None
        seen: set[int] = set()
        cur = style
        while cur is not None:
            el_id = id(cur._element)
            if el_id in seen:   # циклическая цепочка стилей битого .docx
                break
            seen.add(el_id)
            value = getattr(cur.font, attr)   # прямое значение rPr этого стиля
            if value is not None:
                result = value
                break
            cur = cur.base_style

        self._chain_cache[key] = result
        return result

    def paragraph_baseline(self, paragraph, table_style) -> dict[str, bool | None]:
        """Разрешает уровни НИЖЕ рана (стиль абзаца → база → стиль таблицы → база →
        docDefaults) ОДИН РАЗ на абзац — они одинаковы для всех его ран. Возвращает
        {attr: True/False/None}. Вынесено из горячего пути: paragraph.style —
        дорогой геттер python-docx, звать его на каждый ран (×2 атрибута) незачем."""
        para_style = paragraph.style
        baseline: dict[str, bool | None] = {}
        for attr in _CAPS_TAG:
            value = self._style_chain(para_style, attr)
            if value is None and table_style is not None:
                value = self._style_chain(table_style, attr)
            if value is None:
                value = self._docdefaults[attr]
            baseline[attr] = value
        return baseline

    def is_caps(self, run, baseline: dict[str, bool | None]) -> bool:
        """Итог для рана: эффективный all_caps ЛИБО small_caps == True. Приоритет:
        прямой ран → стиль рана (character style) → baseline абзаца-и-ниже. Явный
        False на верхнем уровне отменяет унаследованный True.

        run.style — дорогой геттер, поэтому берём его ЛЕНИВО и только если у рана
        реально задан character style (w:rStyle); у большинства ран его нет, и тогда
        уровень стиля рана пропускается вовсе."""
        rstyle = _MISSING  # ленивое разрешение run.style
        for attr in _CAPS_TAG:
            value = getattr(run.font, attr)   # 1. прямой ран
            if value is None:
                # 2. стиль рана — только если он вообще назначен
                if run._element.style is not None:
                    if rstyle is _MISSING:
                        rstyle = run.style
                    value = self._style_chain(rstyle, attr)
                if value is None:
                    value = baseline[attr]    # 3. абзац-и-ниже (уже разрешён)
            if value is True:
                return True
        return False


def _paragraph_detection_text(paragraph: Paragraph, resolver: "_CapsResolver",
                              table_style=None) -> tuple[str, bool]:
    """Строит detection_text одного параграфа, нормализуя регистр в диапазонах
    ран с флагом all_caps/small_caps и копируя прочие раны как есть.

    Идём по iter_inner_content() (раны и гиперссылки в порядке документа) — именно
    из них python-docx 1.2.0 собирает paragraph.text, поэтому при отсутствии редких
    длина-меняющих символов результат посимвольно совпадает по позициям с
    paragraph.text. Возвращает (detection_text, есть_ли_маркированный_ран)."""
    # Уровни наследования ниже рана (стиль абзаца → база → стиль таблицы → docDefaults)
    # одинаковы для всех ран абзаца — разрешаем их один раз.
    baseline = resolver.paragraph_baseline(paragraph, table_style)
    parts: list[str] = []
    any_flag = False
    for item in paragraph.iter_inner_content():
        runs = item.runs if isinstance(item, Hyperlink) else (item,)
        for run in runs:
            flagged = resolver.is_caps(run, baseline)
            any_flag = any_flag or flagged
            parts.append(_normalize_case(run.text) if flagged else run.text)
    return "".join(parts), any_flag


def _maybe_set_detection_text(metadata: dict, text: str, detection_text: str, segment_id: str) -> None:
    """Хард-гейт равной длины: кладёт detection_text в metadata ТОЛЬКО если его
    длина совпадает с настоящим text. Иначе — не создаёт ключ вообще и логирует
    предупреждение (никогда не пытаемся «подогнать» соответствие)."""
    if len(detection_text) == len(text):
        metadata["detection_text"] = detection_text
    else:
        print(
            f"Предупреждение: detection_text не создан для сегмента {segment_id} — "
            f"нормализация изменила длину "
            f"({len(text)} -> {len(detection_text)}); текст: {text!r}",
            file=sys.stderr,
        )


def _cell_detection_text(cell, resolver: "_CapsResolver", table_style) -> tuple[str, bool]:
    """detection_text ячейки: по параграфам, склеенным '\\n' — ровно как
    _Cell.text = '\\n'.join(p.text for p in cell.paragraphs). table_style
    прокидывается вниз как самый низкий уровень наследования капса для ран ячейки."""
    parts: list[str] = []
    any_flag = False
    for p in cell.paragraphs:
        det, flag = _paragraph_detection_text(p, resolver, table_style)
        parts.append(det)
        any_flag = any_flag or flag
    return "\n".join(parts), any_flag


def _apply_lowercase_heuristic(doc: SourceDocument) -> None:
    """Изменение 2: узкая эвристика для НАСТОЯЩЕГО строчного/заглавного ввода без
    форматирования (одинаково для .docx и .txt). Применяется к сегментам, у
    которых detection_text ещё НЕ установлен Изменением 1, и ТОЛЬКО если сразу:
      * сегмент короткий (< _MAX_HEURISTIC_LEN);
      * весь текст в одном регистре (islower() или isupper());
      * похож на пункт перечисления (_LIST_ITEM_RE).
    Тот же посимвольный механизм и тот же хард-гейт длины, что в Изменении 1.

    ВНИМАНИЕ: это заготовленный паттерн (нумерация) — известная НЕПОКРЫТАЯ ДЫРА:
    ФИО строчными в обычном абзаце БЕЗ нумерации найдено НЕ будет. Осознанно, чтобы
    не гонять NER по всему документу второй раз. См. отчёт, раздел про дыру."""
    for seg in doc.segments:
        text = seg.text
        if not text or "detection_text" in seg.metadata:
            continue
        if len(text) > _MAX_HEURISTIC_LEN:
            continue
        if not (text.islower() or text.isupper()):
            continue
        if not _LIST_ITEM_RE.match(text):
            continue
        _maybe_set_detection_text(seg.metadata, text, _normalize_case(text), seg.id)


def _extract_docx(path: str) -> SourceDocument:
    try:
        document = Document(path)
    except (PackageNotFoundError, zipfile.BadZipFile) as exc:
        # Файл не является валидным .docx-контейнером: переименованный .txt,
        # обрезанный/битый или пустой файл. python-docx кидает PackageNotFoundError
        # (или BadZipFile) — оборачиваем в понятный ValueError, который CLI ловит.
        raise ValueError(
            f"Файл не является корректным .docx (повреждён или неверный формат): {path}"
        ) from exc
    segments: list[TextSegment] = []

    # Один резолвер капса на документ: держит кэш проходов по цепочкам стилей и
    # прочитанные умолчания docDefaults (см. _CapsResolver).
    caps_resolver = _CapsResolver(document)

    paragraph_counter = 0
    table_counter = 0
    seen_tcs = set()

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            style_name = paragraph.style.name if paragraph.style is not None else None
            seg_id = f"p{paragraph_counter}"
            metadata = {"paragraph_index": paragraph_counter, "style": style_name}
            # Изменение 1: detection_text из форматирования all_caps/small_caps
            # (прямого или унаследованного от стиля абзаца/символа).
            det_text, any_flag = _paragraph_detection_text(paragraph, caps_resolver)
            if any_flag:
                _maybe_set_detection_text(metadata, paragraph.text, det_text, seg_id)
            segments.append(
                TextSegment(
                    id=seg_id,
                    text=paragraph.text,
                    source_type="docx_paragraph",
                    metadata=metadata,
                )
            )
            paragraph_counter += 1
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            table_idx = table_counter
            # Стиль таблицы — самый низкий уровень наследования капса для ран ячеек
            # (python-docx не отражает его в run/paragraph style). Берём один раз.
            table_style = table.style
            for row_idx, row in enumerate(table.rows):
                for col_idx, cell in enumerate(row.cells):
                    if len(cell.tables) > 0:
                        print(
                            f"Предупреждение: вложенная таблица в ячейке "
                            f"t{table_idx}_r{row_idx}_c{col_idx} не извлечена (MVP)",
                            file=sys.stderr,
                        )
                    tc = cell._tc
                    cell_seg_id = f"t{table_idx}_r{row_idx}_c{col_idx}"
                    metadata = {
                        "table_index": table_idx,
                        "row_index": row_idx,
                        "col_index": col_idx,
                    }
                    if tc in seen_tcs:
                        cell_text = ""
                    else:
                        seen_tcs.add(tc)
                        cell_text = cell.text
                        # Изменение 1: detection_text ячейки из форматирования ран
                        # (прямого или унаследованного от стиля ячейки/таблицы).
                        det_text, any_flag = _cell_detection_text(cell, caps_resolver, table_style)
                        if any_flag:
                            _maybe_set_detection_text(metadata, cell_text, det_text, cell_seg_id)
                    segments.append(
                        TextSegment(
                            id=cell_seg_id,
                            text=cell_text,
                            source_type="docx_table_cell",
                            metadata=metadata,
                        )
                    )
            table_counter += 1

    return SourceDocument(segments=segments, source_format="docx", source_path=path)


def _looks_like_mojibake(text: str) -> bool:
    """Эвристика тихой порчи: высокая доля управляющих/replacement-символов.

    Байты UTF-16 (особенно UTF-16-LE, где ASCII-символ = <буква>0x00) успешно
    «декодируются» как utf-8 или cp1251 БЕЗ исключения — но результат забит
    NUL'ами и другими C0/C1-управляющими символами, которых в нормальном тексте
    договора нет. Если их доля велика — декодирование заведомо мусорное. Обычный
    UTF-8/cp1251-текст (в т.ч. с эмодзи и переводами строк) даёт долю ~0, поэтому
    порог 0.30 не задевает валидные файлы, но уверенно ловит UTF-16-моджибейк
    (у него доля ~0.5). Не пытаемся УГАДАТЬ UTF-16 без BOM — только не даём
    испорченному тексту тихо пройти как валидный."""
    if not text:
        return False
    bad = 0
    for ch in text:
        code = ord(ch)
        if ch == "�":
            bad += 1  # replacement-символ: часть байтов не декодировалась
        elif code < 0x20 and ch not in "\t\n\r":
            bad += 1  # C0-управляющие (в т.ч. NUL из UTF-16), кроме обычных пробелов
        elif 0x7f <= code <= 0x9f:
            bad += 1  # DEL и C1-управляющие
    return bad / len(text) > 0.30


def _extract_txt(path: str) -> SourceDocument:
    with open(path, "rb") as f:
        data = f.read()

    # UTF-16 с BOM (LE 0xFF 0xFE / BE 0xFE 0xFF) — так блокнот Windows сохраняет
    # "Юникод". Детектируем ДО отката на cp1251: иначе utf-8-sig падает, cp1251
    # молча декодирует UTF-16-байты в моджибейк, и ПДн (ИНН/телефоны/ФИО) не
    # находятся детекторами — тихая порча текста и утечка ПДн в искажённом виде.
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        encoding = "utf-16"
        raw = data.decode(encoding)
    else:
        encoding = "utf-8-sig"
        try:
            raw = data.decode(encoding)
        except UnicodeDecodeError:
            encoding = "cp1251"
            raw = data.decode(encoding)

    # B5-fix: UTF-16 БЕЗ BOM «декодируется» как utf-8-sig/cp1251 без исключения, но
    # в моджибейк — ПДн не находятся и утекают искажёнными без единого предупреждения
    # (тот же класс тихой порчи, что закрывал фикс #7, но #7 ловит только UTF-16 C BOM).
    # Надёжного автоопределения кодировки без BOM не существует, поэтому не угадываем:
    # если ни один из перепробованных вариантов не дал «чистого» текста — поднимаем
    # явную ошибку. Лучше внятный отказ, чем тихая утечка ПДн в LLM.
    if _looks_like_mojibake(raw):
        raise ValueError(
            f"Не удалось надёжно определить кодировку файла: {path}. "
            f"Сохраните файл в UTF-8 и повторите"
        )

    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    segments = [
        TextSegment(
            id=f"l{index}",
            text=line,
            source_type="txt_line",
            metadata={"line_index": index, "encoding": encoding},
        )
        for index, line in enumerate(lines)
    ]

    return SourceDocument(segments=segments, source_format="txt", source_path=path)


def extract(path: str) -> SourceDocument:
    if not Path(path).is_file():
        raise FileNotFoundError(path)

    ext = Path(path).suffix.lower()
    if ext == ".docx":
        doc = _extract_docx(path)
    elif ext == ".txt":
        doc = _extract_txt(path)
    else:
        raise ValueError(f"Неподдерживаемый формат: {ext}. Поддерживаются: .docx, .txt")

    # Изменение 2: узкая эвристика для настоящего строчного/заглавного ввода —
    # для .docx и .txt, поверх сегментов, у которых detection_text не задан.
    _apply_lowercase_heuristic(doc)
    return doc
