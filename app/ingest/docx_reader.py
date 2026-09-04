"""Чтение `.docx` с разрешением наследования стилей.

Почему не хватает `python-docx` «из коробки»:

* **Шрифт и кегль наследуются.** У хорошего решения в параграфах кегль задан
  явно, у слабого — тоже, но проверять надо все три уровня: свойства run,
  стиль параграфа (с цепочкой `basedOn`) и `docDefaults`. Наивное чтение
  `run.font.size` вернёт `None` там, где значение унаследовано, и проверка
  оформления даст ложный «не определено».
* **Межстрочный интервал** у хорошего решения в параграфах не задан вовсе и
  приходит из `styles.xml`. Разрешать обязаны мы, иначе «не определено»
  превращается в «нарушение» на полностью корректной работе.
* **Таблицы** нужно читать структурно. Склейка ячеек в поток параграфов
  уничтожает именно ту структуру, по которой оценивается критерий
  «таблица заполнена корректно».
* **Метаданные бывают не всегда.** У экспорта из Google Docs `docProps/app.xml`
  отсутствует. Отличать «нет данных» от «ноль» обязательно: на этом стоит
  честность сигнала генИИ.
"""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import docx
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.ingest.document import Block, BlockKind, DocMeta, Document, ImageRef

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties/}"
DC = "{http://purl.org/dc/elements/1.1/}"
DCTERMS = "{http://purl.org/dc/terms/}"
EP = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"

# Word хранит кегль в половинах пункта, а межстрочный интервал в двадцатых
# долях пункта (240 = одинарный).
HALF_POINT = 2.0
LINE_UNIT = 240.0


A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _read_theme_fonts(theme_xml: bytes | None) -> dict[str, str]:
    """Соответствие `minorHAnsi`/`majorHAnsi` → реальному имени шрифта.

    Word умеет ссылаться на шрифт темы (`w:asciiTheme="minorHAnsi"`) вместо
    прямого имени. Так сделаны среднее и хорошее решения. Без разбора темы
    шрифт остался бы неопределённым, и проверка «Arial» дала бы «не определено»
    там, где ответ есть.
    """
    if not theme_xml:
        return {}
    root = ET.fromstring(theme_xml)
    out: dict[str, str] = {}
    scheme = root.find(f".//{A}fontScheme")
    if scheme is None:
        return out
    for tag, keys in (
        ("majorFont", ("majorHAnsi", "majorAscii", "majorBidi", "majorEastAsia")),
        ("minorFont", ("minorHAnsi", "minorAscii", "minorBidi", "minorEastAsia")),
    ):
        node = scheme.find(f"{A}{tag}/{A}latin")
        if node is not None and (face := node.get("typeface")):
            for k in keys:
                out[k] = face
    return out


class StyleResolver:
    """Разрешает шрифт, кегль и интервал по цепочке наследования Word.

    Порядок для run: свойства run → стиль run → стиль параграфа (по цепочке
    `basedOn`) → `docDefaults`. Первое найденное значение выигрывает.
    """

    def __init__(self, styles_xml: bytes | None, theme_xml: bytes | None = None) -> None:
        self._styles: dict[str, ET.Element] = {}
        self._default_font: str | None = None
        self._default_size: float | None = None
        self._default_spacing: float | None = None
        self._default_style_id: str | None = None
        self._theme = _read_theme_fonts(theme_xml)
        if styles_xml:
            self._parse(ET.fromstring(styles_xml))

    def _parse(self, root: ET.Element) -> None:
        for st in root.findall(f"{W}style"):
            sid = st.get(f"{W}styleId")
            if sid:
                self._styles[sid] = st
            if st.get(f"{W}type") == "paragraph" and st.get(f"{W}default") == "1":
                self._default_style_id = sid

        defaults = root.find(f"{W}docDefaults")
        if defaults is None:
            return
        if (rpr := defaults.find(f"{W}rPrDefault/{W}rPr")) is not None:
            self._default_font = self._font_of(rpr)
            self._default_size = _size_of(rpr)
        if (ppr := defaults.find(f"{W}pPrDefault/{W}pPr")) is not None:
            self._default_spacing = _spacing_of(ppr)

    def _chain(self, style_id: str | None) -> list[ET.Element]:
        """Стиль и его предки по `basedOn`, от частного к общему."""
        out: list[ET.Element] = []
        seen: set[str] = set()
        cur = style_id
        while cur and cur not in seen and cur in self._styles:
            seen.add(cur)
            st = self._styles[cur]
            out.append(st)
            based = st.find(f"{W}basedOn")
            cur = based.get(f"{W}val") if based is not None else None
        return out

    def _font_of(self, rpr: ET.Element) -> str | None:
        """Имя шрифта из `w:rFonts`, включая ссылку на шрифт темы."""
        fonts = rpr.find(f"{W}rFonts")
        if fonts is None:
            return None
        # Кириллица приходит через ascii/hAnsi; cs/eastAsia — запасные варианты.
        for attr in ("ascii", "hAnsi", "cs", "eastAsia"):
            if v := fonts.get(f"{W}{attr}"):
                return v
        # Прямого имени нет — документ ссылается на шрифт темы.
        for attr in ("asciiTheme", "hAnsiTheme", "cstheme", "eastAsiaTheme"):
            if (v := fonts.get(f"{W}{attr}")) and (face := self._theme.get(v)):
                return face
        return None

    def font(self, run_rpr: ET.Element | None, style_id: str | None) -> str | None:
        if run_rpr is not None and (f := self._font_of(run_rpr)):
            return f
        for st in self._chain(style_id):
            if (rpr := st.find(f"{W}rPr")) is not None and (f := self._font_of(rpr)):
                return f
        for st in self._chain(self._default_style_id):
            if (rpr := st.find(f"{W}rPr")) is not None and (f := self._font_of(rpr)):
                return f
        return self._default_font

    def size_pt(self, run_rpr: ET.Element | None, style_id: str | None) -> float | None:
        if run_rpr is not None and (s := _size_of(run_rpr)) is not None:
            return s
        for st in self._chain(style_id):
            if (rpr := st.find(f"{W}rPr")) is not None and (s := _size_of(rpr)) is not None:
                return s
        for st in self._chain(self._default_style_id):
            if (rpr := st.find(f"{W}rPr")) is not None and (s := _size_of(rpr)) is not None:
                return s
        return self._default_size

    def spacing(self, p_ppr: ET.Element | None, style_id: str | None) -> float | None:
        """Межстрочный интервал как множитель (1.15, 1.5, …) или `None`.

        `None` значит «не определено» — валидный ответ, который формальный
        слой обязан отличать от нарушения.
        """
        if p_ppr is not None and (s := _spacing_of(p_ppr)) is not None:
            return s
        for st in self._chain(style_id):
            if (ppr := st.find(f"{W}pPr")) is not None and (s := _spacing_of(ppr)) is not None:
                return s
        for st in self._chain(self._default_style_id):
            if (ppr := st.find(f"{W}pPr")) is not None and (s := _spacing_of(ppr)) is not None:
                return s
        return self._default_spacing



def _size_of(rpr: ET.Element) -> float | None:
    sz = rpr.find(f"{W}sz")
    if sz is None or not (v := sz.get(f"{W}val")):
        return None
    try:
        return float(v) / HALF_POINT
    except ValueError:
        return None


def _spacing_of(ppr: ET.Element) -> float | None:
    sp = ppr.find(f"{W}spacing")
    if sp is None:
        return None
    line = sp.get(f"{W}line")
    if not line:
        return None
    rule = sp.get(f"{W}lineRule", "auto")
    # `exact`/`atLeast` задают интервал в пунктах, а не множителем.
    # Пересчёт в множитель без кегля некорректен — честнее вернуть «не определено».
    if rule in {"exact", "atLeast"}:
        return None
    try:
        return round(float(line) / LINE_UNIT, 3)
    except ValueError:
        return None


def _iter_body(doc: DocxDocument):
    """Параграфы и таблицы в исходном порядке документа.

    `doc.paragraphs` и `doc.tables` — два отдельных плоских списка, порядок
    между ними теряется. Для нумерации `§N`, по которой модель цитирует
    работу, порядок обязателен.
    """
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def _para_facts(p: Paragraph, res: StyleResolver) -> tuple[set[str], set[float], float | None]:
    style_id = p.style.style_id if p.style is not None else None
    fonts: set[str] = set()
    sizes: set[float] = set()
    for run in p.runs:
        if not run.text.strip():
            continue  # пробельные run'ы часто несут чужое форматирование
        rpr = run._element.find(f"{W}rPr")
        if f := res.font(rpr, style_id):
            fonts.add(f)
        if (s := res.size_pt(rpr, style_id)) is not None:
            sizes.add(s)
    ppr = p._element.find(f"{W}pPr")
    return fonts, sizes, res.spacing(ppr, style_id)


def _is_heading(p: Paragraph) -> bool:
    name = (p.style.name if p.style is not None else "") or ""
    return name.lower().startswith(("heading", "заголовок", "title", "название"))


def _is_list(p: Paragraph) -> bool:
    ppr = p._element.find(f"{W}pPr")
    return ppr is not None and ppr.find(f"{W}numPr") is not None


def _read_meta(zf: zipfile.ZipFile) -> DocMeta:
    names = set(zf.namelist())
    meta = DocMeta(
        has_app_xml="docProps/app.xml" in names,
        has_core_xml="docProps/core.xml" in names,
    )

    if meta.has_core_xml:
        core = ET.fromstring(zf.read("docProps/core.xml"))
        meta.author = _text(core.find(f"{DC}creator"))
        meta.last_modified_by = _text(core.find(f"{CP}lastModifiedBy"))
        meta.created = _dt(_text(core.find(f"{DCTERMS}created")))
        meta.modified = _dt(_text(core.find(f"{DCTERMS}modified")))
        if rev := _text(core.find(f"{CP}revision")):
            meta.revision = _int(rev)

    if meta.has_app_xml:
        app = ET.fromstring(zf.read("docProps/app.xml"))
        meta.total_time_min = _int(_text(app.find(f"{EP}TotalTime")))
        meta.words = _int(_text(app.find(f"{EP}Words")))
        meta.pages = _int(_text(app.find(f"{EP}Pages")))
        meta.application = _text(app.find(f"{EP}Application"))

    return meta


def _text(el: ET.Element | None) -> str | None:
    return el.text.strip() if el is not None and el.text else None


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_docx(path: str | Path) -> Document:
    """Разбирает `.docx` в `Document`."""
    path = Path(path)
    doc = docx.Document(str(path))
    warnings: list[str] = []

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        meta = _read_meta(zf)
        resolver = StyleResolver(
            zf.read("word/styles.xml") if "word/styles.xml" in names else None,
            zf.read("word/theme/theme1.xml") if "word/theme/theme1.xml" in names else None,
        )
        images = [
            ImageRef(name=n.split("/")[-1], size_bytes=zf.getinfo(n).file_size)
            for n in names
            if n.startswith("word/media/")
        ]
        raw_xml = zf.read("word/document.xml").decode("utf-8", errors="replace")

    if not meta.has_app_xml:
        warnings.append(
            "Метаданные docProps/app.xml отсутствуют — вероятен экспорт из Google Docs. "
            "Время редактирования и число слов недоступны, сигнал генИИ по метаданным "
            "неприменим (это «недоступно», а не «признаков нет»)."
        )
    if not meta.has_core_xml:
        warnings.append("Метаданные docProps/core.xml отсутствуют: автор и даты недоступны.")

    blocks: list[Block] = []
    idx = 0
    for item in _iter_body(doc):
        if isinstance(item, Paragraph):
            text = item.text.strip()
            if not text:
                continue
            idx += 1
            fonts, sizes, spacing = _para_facts(item, resolver)
            kind = (
                BlockKind.HEADING
                if _is_heading(item)
                else BlockKind.LIST_ITEM
                if _is_list(item)
                else BlockKind.PARAGRAPH
            )
            blocks.append(
                Block(
                    index=idx,
                    kind=kind,
                    text=text,
                    style=(item.style.name if item.style is not None else ""),
                    fonts=fonts,
                    sizes_pt=sizes,
                    line_spacing=spacing,
                )
            )
        else:  # Table
            rows: list[list[str]] = []
            fonts: set[str] = set()
            sizes: set[float] = set()
            spacings: list[float] = []
            for row in item.rows:
                cells: list[str] = []
                for cell in row.cells:
                    cells.append(" ".join(p.text.strip() for p in cell.paragraphs).strip())
                    for p in cell.paragraphs:
                        f, s, sp = _para_facts(p, resolver)
                        fonts |= f
                        sizes |= s
                        if sp is not None:
                            spacings.append(sp)
                rows.append(cells)
            if not any(any(c for c in r) for r in rows):
                continue
            idx += 1
            blocks.append(
                Block(
                    index=idx,
                    kind=BlockKind.TABLE,
                    text="\n".join(" | ".join(r) for r in rows),
                    table=rows,
                    fonts=fonts,
                    sizes_pt=sizes,
                    line_spacing=(min(spacings) if spacings else None),
                )
            )

    if images:
        warnings.append(
            f"В работе {len(images)} изображени(е/я) — схемы не анализируются текстовой "
            f"моделью. В отчёте помечаются как «изображение не оценено»."
        )

    facts: dict[str, object] = {
        "block_count": len(blocks),
        "table_count": sum(1 for b in blocks if b.kind is BlockKind.TABLE),
        "image_count": len(images),
        "has_images": bool(images),
        # Признак истории изменений: условие требует доступ на редактирование
        # с видимой историей. Внутри файла это отражается правками и примечаниями.
        "has_tracked_changes": ("<w:ins " in raw_xml or "<w:del " in raw_xml),
        "has_comments": "word/comments.xml" in names,
        "revision": meta.revision,
    }

    return Document(
        source_name=path.name,
        source_format="docx",
        blocks=blocks,
        meta=meta,
        images=images,
        facts=facts,
        warnings=warnings,
    )
