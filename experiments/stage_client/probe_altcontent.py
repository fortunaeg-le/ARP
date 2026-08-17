# -*- coding: utf-8 -*-
"""ЧАСТЬ 2 разведки этапа CLIENT: что лежит в mc:AlternateContent и как на него
реагирует сканер зон.

Строит три .docx СВОИМИ РУКАМИ (реальные документы не используются):
  1. alt_text     — фигура с надписью: mc:Choice (wps/DrawingML) + mc:Fallback (VML),
                    ОДИН И ТОТ ЖЕ текст в обеих ветвях (так пишет Word);
  2. alt_notext   — та же обёртка, но внутри ФИГУРА БЕЗ ТЕКСТА (линия/прямоугольник):
                    ни одного w:t, ни одного символа;
  3. alt_only_fb  — только mc:Fallback с текстом (искусственный случай).

Печатает: сколько зон видит scan_unread_zones, каких видов, и что при этом
вернул extract() (сколько сегментов, есть ли текст фигуры).
"""
import os
import sys
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(1, os.path.join(_ROOT, "src"))

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_out")

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/charts/chart1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>
</Types>"""

# Заглушки частей, на которые ссылаются связи: python-docx открывает пакет
# целиком и падает, если объявленная связь никуда не ведёт. Диаграмма несёт
# ТЕКСТ — ровно то содержимое, которого сканер зон не видит и ради которого
# отказ на нерастровой связи обязан сохраниться.
_PNG = bytes.fromhex("89504e470d0a1a0a")
_CHART = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    "<c:chart><c:title><c:tx><c:rich><a:p><a:r>"
    "<a:t>Иванов Иван Иванович, ИНН 7707083893</a:t>"
    "</a:r></a:p></c:rich></c:tx></c:title></c:chart></c:chartSpace>"
)

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

# Связи САМОГО word/document.xml: картинка (текста не несёт) и диаграмма
# (текст лежит в отдельной части, которую мы не смотрели).
_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rIdImg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
<Relationship Id="rIdChart" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="charts/chart1.xml"/>
</Relationships>"""

_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:w10="urn:schemas-microsoft-com:office:word" '
    'mc:Ignorable="w14 wp14"'
)


def _choice(txbx_inner: str) -> str:
    return (
        '<mc:Choice Requires="wps"><w:drawing><wp:inline><a:graphic><a:graphicData '
        'uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        "<wps:wsp><wps:spPr/>" + txbx_inner + "</wps:wsp>"
        "</a:graphicData></a:graphic></wp:inline></w:drawing></mc:Choice>"
    )


def _fallback(txbx_inner: str) -> str:
    return (
        "<mc:Fallback><w:pict><v:shape>" + txbx_inner + "</v:shape></w:pict></mc:Fallback>"
    )


def _txbx(tag: str, text: str) -> str:
    """tag: 'wps:txbx' (DrawingML) или 'v:textbox' (VML)."""
    return (
        f"<{tag}><w:txbxContent><w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        f"</w:txbxContent></{tag}>"
    )


TEXT = "Иванов Иван Иванович, ИНН 7707083893"

BODIES = {
    # 1. текст в ОБЕИХ ветвях — так пишет настоящий Word
    "alt_text": (
        "<w:p><w:r><w:t>Тело договора.</w:t></w:r>"
        "<mc:AlternateContent>"
        + _choice(_txbx("wps:txbx", TEXT))
        + _fallback(_txbx("v:textbox", TEXT))
        + "</mc:AlternateContent></w:p>"
    ),
    # 2. фигура БЕЗ текста: ни одного w:t внутри обёртки
    "alt_notext": (
        "<w:p><w:r><w:t>Тело договора.</w:t></w:r>"
        "<mc:AlternateContent>"
        + _choice("<wps:spPr/>")
        + _fallback('<w:pict><v:shape style="width:100pt;height:1pt"/></w:pict>')
        + "</mc:AlternateContent></w:p>"
    ),
    # 3. текст только в запасной ветви
    "alt_only_fb": (
        "<w:p><w:r><w:t>Тело договора.</w:t></w:r>"
        "<mc:AlternateContent>"
        + _choice("<wps:spPr/>")
        + _fallback(
            "<w:pict><v:shape>" + _txbx("v:textbox", TEXT) + "</v:shape></w:pict>"
        )
        + "</mc:AlternateContent></w:p>"
    ),
    # 4. фигура-КАРТИНКА без текста: ссылка ведёт на связь типа «изображение».
    #    Растр текста не несёт, обычная картинка в теле отказа не вызывает —
    #    обёртка не должна менять вердикт.
    "alt_image": (
        "<w:p><w:r><w:t>Тело договора.</w:t></w:r>"
        "<mc:AlternateContent>"
        + _choice('<wps:spPr><a:blip xmlns:r="http://schemas.openxmlformats.org/'
                  'officeDocument/2006/relationships" r:embed="rIdImg"/></wps:spPr>')
        + _fallback('<w:pict><v:shape style="width:100pt;height:50pt"/></w:pict>')
        + "</mc:AlternateContent></w:p>"
    ),
    # 5. ДИАГРАММА без текста в document.xml: текст лежит в charts/chart1.xml,
    #    которую мы не смотрели. Обязан остаться отказ.
    "alt_chart": (
        "<w:p><w:r><w:t>Тело договора.</w:t></w:r>"
        "<mc:AlternateContent>"
        + _choice('<wps:spPr><c:chart xmlns:c="http://schemas.openxmlformats.org/'
                  'drawingml/2006/chart" xmlns:r="http://schemas.openxmlformats.org/'
                  'officeDocument/2006/relationships" r:id="rIdChart"/></wps:spPr>')
        + _fallback('<w:pict><v:shape style="width:100pt;height:50pt"/></w:pict>')
        + "</mc:AlternateContent></w:p>"
    ),
    # 6. WordArt: надпись живёт в АТРИБУТЕ v:textpath/@string, itertext() её не
    #    видит. Проверка текстонесущих атрибутов обязана поймать.
    "alt_wordart": (
        "<w:p><w:r><w:t>Тело договора.</w:t></w:r>"
        "<mc:AlternateContent>"
        + _choice("<wps:spPr/>")
        + '<mc:Fallback><w:pict><v:shape><v:textpath string="'
        + TEXT + '"/></v:shape></w:pict></mc:Fallback>'
        + "</mc:AlternateContent></w:p>"
    ),
}


def build(name: str, body: str) -> str:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name + ".docx")
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_NS}><w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/media/image1.png", _PNG)
        z.writestr("word/charts/chart1.xml", _CHART)
    return path


def main() -> None:
    from unread_zones import scan_unread_zones
    from extractor import extract

    for name, body in BODIES.items():
        path = build(name, body)
        print("=" * 70)
        print(name)
        zones = scan_unread_zones(path)
        print(f"  зон: {len(zones)}")
        for z in zones:
            print(f"    kind={z.kind:12s} detail={z.detail or '-':20s} "
                  f"chars={z.char_count:4d} preview={z.text_preview[:60]!r}")
        doc = extract(path)
        print(f"  сегментов extract(): {len(doc.segments)}")
        for s in doc.segments:
            print(f"    {s.id}: {s.text!r}")


if __name__ == "__main__":
    main()
