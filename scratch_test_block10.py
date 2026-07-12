"""Разовый скрипт для ручной проверки блока 10 (pptx_rewriter.py).

Не часть модуля, не зависимость. Собирает синтетический .pptx вручную через
zipfile (python-pptx не установлен и не должен быть зависимостью блока),
прогоняет rewrite() и проверяет несколько сценариев из раздела "Приёмка".
"""
import os
import shutil
import zipfile

CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
<Override PartName="/ppt/notesSlides/notesSlide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>
<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
</Types>"""

RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>"""

PRES_RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>
</Relationships>"""

SLIDE_RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>"""

PRESENTATION = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId2"/></p:sldMasterIdLst>
<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
<p:sldSz cx="9144000" cy="6858000"/>
</p:presentation>"""

SLIDE_MASTER = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld><p:spTree/></p:cSld>
</p:sldMaster>"""

SLIDE_LAYOUT = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld><p:spTree/></p:cSld>
</p:sldLayout>"""

THEME = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Test"/>"""

# Слайд: заголовок [ORG_1] крупным фирменным цветом, в теле - [INN_1],
# токен разбит между двумя <a:r> внутри таблицы, и неизвестный токен [ORG_99].
SLIDE1 = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld>
<p:spTree>
<p:sp>
<p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
<p:spPr/>
<p:txBody>
<a:p><a:r><a:rPr lang="ru-RU" sz="4400" b="1"><a:solidFill><a:srgbClr val="FF0000"/></a:solidFill></a:rPr><a:t>[ORG_1]</a:t></a:r></a:p>
</p:txBody>
</p:sp>
<p:sp>
<p:nvSpPr><p:cNvPr id="3" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
<p:spPr/>
<p:txBody>
<a:p><a:r><a:rPr lang="ru-RU" sz="1800"/><a:t>INN: [INN_1]</a:t></a:r></a:p>
<a:p><a:r><a:rPr lang="ru-RU"/><a:t>Unknown: [ORG_99] here</a:t></a:r></a:p>
<a:p><a:r><a:rPr lang="ru-RU"/><a:t>Line1</a:t></a:r><a:br><a:rPr lang="ru-RU"/></a:br><a:r><a:rPr lang="ru-RU"/><a:t>Line2</a:t></a:r></a:p>
</p:txBody>
</p:sp>
<p:graphicFrame>
<p:nvGraphicFramePr><p:cNvPr id="4" name="Table"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>
<p:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></p:xfrm>
<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">
<a:tbl>
<a:tr h="0"><a:tc><a:txBody><a:bodyPr/><a:p><a:r><a:rPr lang="ru-RU"/><a:t>[PHONE</a:t></a:r><a:r><a:rPr lang="ru-RU"/><a:t>_1] cell</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
</a:tbl>
</a:graphicData></a:graphic>
</p:graphicFrame>
</p:spTree>
</p:cSld>
</p:sld>"""

NOTES1 = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:rPr lang="ru-RU"/><a:t>Заметка: [ORG_1]</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
</p:notes>""".encode("utf-8")

DOCPROPS_CORE = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"/>"""

PARTS = {
    "[Content_Types].xml": CONTENT_TYPES,
    "_rels/.rels": RELS,
    "docProps/core.xml": DOCPROPS_CORE,
    "ppt/presentation.xml": PRESENTATION,
    "ppt/_rels/presentation.xml.rels": PRES_RELS,
    "ppt/slideMasters/slideMaster1.xml": SLIDE_MASTER,
    "ppt/slideLayouts/slideLayout1.xml": SLIDE_LAYOUT,
    "ppt/theme/theme1.xml": THEME,
    "ppt/slides/slide1.xml": SLIDE1,
    "ppt/slides/_rels/slide1.xml.rels": SLIDE_RELS,
    "ppt/notesSlides/notesSlide1.xml": NOTES1,
}

SRC = "scratch_test_block10_src.pptx"
DST = "scratch_test_block10_dst.pptx"


def build_src():
    if os.path.exists(SRC):
        os.remove(SRC)
    with zipfile.ZipFile(SRC, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in PARTS.items():
            z.writestr(name, data)


def main():
    build_src()
    if os.path.exists(DST):
        os.remove(DST)

    from pptx_rewriter import rewrite

    mapping = {
        "[ORG_1]": "ООО «Ромашка»",
        "[INN_1]": "7701234567",
        "[PHONE_1]": "+7 495 123-45-67",
    }
    unresolved = rewrite(SRC, DST, lambda tok: mapping.get(tok))
    print("unresolved:", unresolved)
    assert unresolved == ["[ORG_99]"], unresolved

    with zipfile.ZipFile(DST) as z:
        names_dst = set(z.namelist())
        names_src = set(PARTS.keys())
        assert names_dst == names_src, (names_dst ^ names_src)
        assert z.testzip() is None

        slide1 = z.read("ppt/slides/slide1.xml").decode("utf-8")
        notes1 = z.read("ppt/notesSlides/notesSlide1.xml").decode("utf-8")
        theme = z.read("ppt/theme/theme1.xml")
        master = z.read("ppt/slideMasters/slideMaster1.xml")

    assert "ООО «Ромашка»" in slide1
    assert "sz=\"4400\"" in slide1 and "FF0000" in slide1
    assert "INN: 7701234567" in slide1
    assert "[ORG_99]" in slide1  # unknown token left as-is
    # значение попадает в run, где начинался токен; хвост " cell" остаётся в
    # следующем run нетронутым (правило распределения по узлам, блок 8)
    assert "<a:t>+7 495 123-45-67</a:t>" in slide1
    assert '<a:t xml:space="preserve"> cell</a:t>' in slide1
    assert "Заметка: ООО «Ромашка»" in notes1
    assert theme == THEME
    assert master == SLIDE_MASTER  # slideMaster untouched (not in mask)

    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
