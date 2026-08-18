"""PDF-ARCH, задача 2 — гарантия полноты чтения PDF (ветка А, текстовый слой).

Природа неполноты у PDF ДРУГАЯ, чем у .docx (`unread_zones.py`): там —
непрочитанные XML-узлы пакета, здесь — страницы, у которых текстового слоя
попросту НЕТ (скан, картинка) или он есть, но раскодирован в мусор. Свой OCR
в этот блок не входит (решение владельца, PDF-ARCH ветка А) — значит такие
страницы система прочитать не может в принципе, и единственный честный ответ —
не отказ, а ГРОМКОЕ предупреждение с точным указанием, что именно пропущено.

Политика для PDF ОСОЗНАННО отличается от .docx-strict (см. `unread_zones.py`
и `shifrator._check_unread_zones`): там пропуск зоны — это дефект чтения
СВОЕГО формата (закрытый список узлов, который система обязана уметь
разобрать), и отказ на пути к тихой потере оправдан. Здесь пропуск — предел
самого формата (сканы) или входных данных (испорченный PDF), который никаким
чтением не устраняется; отказывать в работе над всем документом из-за одной
страницы-скана значило бы никогда не анонимизировать текстовые страницы
рядом с ней. Поэтому PDF-путь ВСЕГДА lossy: работает, предупреждает, никогда
не бросает `UnreadZoneError`-подобное исключение наверх у `cmd_encrypt`.

Архитектурная развилка (см. отчёт этапа pdf-read): можно было обобщить это в
контракт `extract()` («каждый поставщик текста возвращает документ вместе со
своей неполнотой»), но эта версия — узкая PDF-пристройка сбоку, вызываемая
явно из `shifrator.cmd_encrypt` после `extract()`, ПОДОБНО тому как
`unread_zones.scan_unread_zones` вызывается отдельно от `_extract_docx`.
Явная причина: не трогать работающий .docx-путь и не расширять контракт
`SourceDocument`/`extract()` под один формат за 90-минутный бюджет; если
появится третий формат с похожей неполнотой — это будет сигналом обобщить.
"""

from dataclasses import dataclass, field

from extractor import _looks_like_mojibake

_MOJIBAKE_THRESHOLD = 0.30


@dataclass
class PdfIssue:
    """Одна проблемная страница PDF.

    page_number — НОМЕР СТРАНИЦЫ (1-based, как видит его человек в читалке).
    kind — "no_text_layer" | "image_only" | "mojibake" (машиночитаемый признак
    для интерфейса — не парсить текст предупреждения из строки).
    detail — короткая техническая причина (для таблицы/лога).
    legal_message — формулировка ДЛЯ ЮРИСТА: что это значит по-русски простыми
    словами и чем грозит при передаче текста в стороннюю систему.
    """

    page_number: int
    kind: str
    detail: str
    legal_message: str
    char_count: int = 0
    text: str = field(default="", repr=False)


def _page_has_image_xobject(page) -> bool:
    """Есть ли в ресурсах страницы картинка (XObject /Subtype /Image).

    Не пытаемся угадать «похоже на скан» иначе, чем через факт наличия
    растрового объекта: страница без текста И без картинки (пустая страница
    договора, разделитель разделов) — тоже неполнота, но другого рода
    ('no_text_layer', не 'image_only') — причина в предупреждении обязана
    быть точной, а не гадательной.
    """
    try:
        resources = page.get("/Resources")
        if resources is None:
            return False
        xobjects = resources.get("/XObject")
        if not xobjects:
            return False
        for ref in xobjects.values():
            obj = ref.get_object() if hasattr(ref, "get_object") else ref
            if obj.get("/Subtype") == "/Image":
                return True
    except Exception:
        # Повреждённые/нестандартные ресурсы — не наша забота здесь: если
        # текста тоже нет, страница всё равно попадёт в no_text_layer.
        return False
    return False


def scan_pdf_completeness(path: str) -> "list[PdfIssue]":
    """Находит в PDF страницы, которые extract() не смог прочитать полностью.

    Возвращает список PdfIssue (пустой — все страницы с текстовым слоем и без
    признаков порчи). Причины по каждой странице:
      * no_text_layer — extract_text() пуст, картинки в ресурсах нет (нет и
        текста, и очевидной причины — раздел размечен вёрсткой, а не текстом);
      * image_only — extract_text() пуст, но есть XObject-картинка (похоже
        на скан/фото страницы без OCR-слоя);
      * mojibake — текст есть, но велика доля control/replacement-символов
        (шрифт с нестандартным кодированием — та же эвристика, что и у .txt,
        см. extractor._looks_like_mojibake, порог 0.30).

    Битый/зашифрованный PDF пусть падает через ValueError из extractor.extract
    ДО вызова этой функции (см. shifrator.cmd_encrypt) — здесь мы уже держим
    в руках документ, который extract() открыл успешно.
    """
    from pypdf import PdfReader

    reader = PdfReader(path)
    issues: "list[PdfIssue]" = []
    for index, page in enumerate(reader.pages):
        page_number = index + 1
        text = page.extract_text() or ""
        stripped = text.strip()
        if not stripped:
            if _page_has_image_xobject(page):
                issues.append(PdfIssue(
                    page_number=page_number,
                    kind="image_only",
                    detail="похоже на скан/изображение без OCR",
                    legal_message=(
                        f"страница {page_number}: похоже на скан или фотографию "
                        f"страницы без распознанного текста. Эта страница НЕ была "
                        f"прочитана и НЕ была анонимизирована — при передаче текста "
                        f"в стороннюю систему проверьте вручную, не содержит ли эта "
                        f"страница персональные данные или реквизиты."
                    ),
                ))
            else:
                issues.append(PdfIssue(
                    page_number=page_number,
                    kind="no_text_layer",
                    detail="нет текстового слоя",
                    legal_message=(
                        f"страница {page_number}: нет текстового слоя — на ней не "
                        f"нашлось ни текста, ни изображения, которые можно было бы "
                        f"прочитать. Эта страница НЕ была анонимизирована — при "
                        f"передаче текста в стороннюю систему проверьте вручную, не "
                        f"содержит ли эта страница персональные данные или реквизиты."
                    ),
                ))
            continue

        bad = sum(
            1 for ch in text
            if ch == "�" or (ord(ch) < 0x20 and ch not in "\t\n\r")
            or 0x7f <= ord(ch) <= 0x9f
        )
        ratio = bad / len(text) if text else 0.0
        if ratio > _MOJIBAKE_THRESHOLD:
            pct = round(ratio * 100)
            issues.append(PdfIssue(
                page_number=page_number,
                kind="mojibake",
                detail=f"{pct}% нераспознанных глифов",
                legal_message=(
                    f"страница {page_number}: {pct}% символов текста не удалось "
                    f"надёжно распознать (повреждённое или нестандартное "
                    f"кодирование шрифта в файле). Часть текста этой страницы НЕ "
                    f"была анонимизирована корректно — при передаче текста в "
                    f"стороннюю систему проверьте эту страницу вручную на предмет "
                    f"персональных данных и реквизитов."
                ),
                char_count=len(text),
                text=text,
            ))
    return issues


def issues_table(issues: "list[PdfIssue]") -> str:
    """Человекочитаемая таблица «страница / тип / причина» — печатается ТУДА
    же, куда обезличенный текст и session_id (тот же вывод, не отдельный лог)."""
    rows = [("СТРАНИЦА", "ПРИЧИНА")]
    for i in issues:
        rows.append((str(i.page_number), i.detail))
    widths = [max(len(r[0]) for r in rows), max(len(r[1]) for r in rows)]
    out = []
    for idx, r in enumerate(rows):
        out.append("  %-*s  %-*s" % (widths[0], r[0], widths[1], r[1]))
        if idx == 0:
            out.append("  " + "-" * (sum(widths) + 2))
    return "\n".join(out)


def legal_summary(issues: "list[PdfIssue]") -> str:
    """Формулировка для юриста — что это значит и чем грозит. Собирает
    per-issue legal_message построчно (каждый уже написан простыми словами)."""
    if not issues:
        return ""
    lines = [
        f"ПРЕДУПРЕЖДЕНИЕ: часть текста документа ({len(issues)} стр.) не могла "
        f"быть прочитана и НЕ была анонимизирована:",
    ]
    lines.extend(i.legal_message for i in issues)
    return "\n".join(lines)


def issues_to_json(issues: "list[PdfIssue]") -> "list[dict]":
    """Список проблем для sidecar {sid}.unread.json — с сырым текстом там, где
    он есть (mojibake), чтобы пользователь видел, что именно пострадало."""
    return [
        {
            "page_number": i.page_number,
            "kind": i.kind,
            "detail": i.detail,
            "legal_message": i.legal_message,
            "char_count": i.char_count,
            "text": i.text,
        }
        for i in issues
    ]
