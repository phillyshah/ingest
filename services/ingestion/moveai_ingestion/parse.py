"""Parse stage: structure (headings, tables, anchors, lists, pages) with phase associations preserved (spec §5.4)."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from pypdf import PdfReader
from selectolax.parser import HTMLParser

from .config import MAX_GRAPHIC_BYTES, MAX_IMAGES_PER_DOCUMENT


@dataclass
class Block:
    kind: str  # heading | paragraph | list | table | image | footnote | ocr
    text: str
    locator: dict[str, Any]
    level: int | None = None
    heading_path: list[str] = field(default_factory=list)  # enclosing headings at the time of the block
    rows: list[list[str]] | None = None
    caption: str | None = None
    confidence: float | None = None
    alternatives: list[str] | None = None
    page: int | None = None


@dataclass
class ExtractedImage:
    """An embedded image pulled out of a source document, with its real bytes.

    Separate from `Block`: a block is text structure fed to the extraction model, and the model is never shown
    image bytes (spec §5: schema-only text output, no tools) or told a `src` for content that never had one — a
    PDF's embedded images have no URL to report. Association back to an exercise is done deterministically from
    `page`, in code, never guessed by the model (see pipeline.py stage_extract/persist).
    """

    page: int
    index: int
    data: bytes
    content_type: str
    sha256: str


@dataclass
class ParsedDocument:
    blocks: list[Block]
    title: str | None
    declared_publication_date: str | None
    warnings: list[str] = field(default_factory=list)
    kind: str = "html"
    images: list[ExtractedImage] = field(default_factory=list)

    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


def parse_html(content: bytes) -> ParsedDocument:
    tree = HTMLParser(content.decode("utf-8", errors="replace"))
    blocks: list[Block] = []
    heading_stack: list[tuple[int, str]] = []
    title = tree.css_first("title").text(strip=True) if tree.css_first("title") else None
    body = tree.body or tree.root
    if body is None:
        return ParsedDocument([], title, None, ["empty document"])

    # `traverse`, not `iter`: iter() yields only the DIRECT children of <body>. Every fixture in this repo puts its
    # headings and paragraphs straight under <body>, so the tests passed while any real page — which always wraps
    # its content in at least one <div> — produced zero blocks and therefore no claims, no dose fields, nothing.
    # The failure was silent: an empty parse is indistinguishable from a page with nothing to say.
    #
    # Removing script/style up front rather than skipping them in the loop, because a skipped node's children are
    # still traversed and script text would otherwise be read as prose.
    for junk in body.css("script, style, noscript, template"):
        junk.decompose()

    # A table's cells and a list's items are already captured whole by their container block below. Without this,
    # descending into them would emit every <p> inside a <td> a second time, duplicating claims and their locators.
    # Ancestor tags rather than node identity: selectolax builds a fresh Python wrapper on each access, so `id()`
    # and `is` are not stable across `.parent` walks — only `==` is, and tag names are enough here.
    def inside_container(node: Any) -> bool:
        parent = node.parent
        while parent is not None:
            if parent.tag in ("table", "ol", "ul"):
                return True
            parent = parent.parent
        return False

    for node in body.traverse(include_text=False):
        tag = node.tag
        if inside_container(node):
            continue
        if tag in ("h1", "h2", "h3", "h4"):
            level = int(tag[1])
            text = node.text(strip=True)
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, text))
            blocks.append(
                Block(
                    "heading",
                    text,
                    {
                        "anchor": f"#{node.attributes.get('id')}" if node.attributes.get("id") else None,
                        "css": tag,
                    },
                    level=level,
                    heading_path=[h[1] for h in heading_stack[:-1]],
                )
            )
        elif tag == "p":
            blocks.append(
                Block(
                    "footnote" if "footnote" in (node.attributes.get("class") or "") else "paragraph",
                    node.text(strip=True),
                    {"css": "p"},
                    heading_path=[h[1] for h in heading_stack],
                )
            )
        elif tag in ("ol", "ul"):
            items = [li.text(strip=True) for li in node.css("li")]
            blocks.append(Block("list", "\n".join(items), {"css": tag}, heading_path=[h[1] for h in heading_stack]))
        elif tag == "table":
            rows = [[c.text(strip=True) for c in tr.css("th,td")] for tr in node.css("tr")]
            cap = node.css_first("caption")
            blocks.append(
                Block(
                    "table",
                    cap.text(strip=True) if cap else "",
                    {"table": f"#{node.attributes.get('id')}" if node.attributes.get("id") else None},
                    heading_path=[h[1] for h in heading_stack],
                    rows=rows,
                    caption=cap.text(strip=True) if cap else None,
                )
            )
        elif tag == "img":
            blocks.append(
                Block(
                    "image",
                    node.attributes.get("alt") or "",
                    {
                        "anchor": f"#{node.attributes.get('id')}" if node.attributes.get("id") else None,
                        "src": node.attributes.get("src"),
                    },
                    heading_path=[h[1] for h in heading_stack],
                )
            )
    date = None
    for b in blocks:
        if b.kind == "paragraph" and "Published:" in b.text:
            date = b.text.split("Published:")[1].strip().split()[0].rstrip(".")
    return ParsedDocument(blocks, title, date, kind="html")


def _extract_page_images(page: Any, pno: int, warnings: list[str]) -> list[ExtractedImage]:
    """Pull the real, embedded images off one PDF page (spec: uploaded protocol sheets often carry exercise
    photos inline). Best-effort: a PDF with an image pypdf cannot decode should not fail the whole document."""
    out: list[ExtractedImage] = []
    try:
        images = page.images
    except Exception as e:  # noqa: BLE001 - a malformed embedded image must not sink the whole document
        warnings.append(f"page {pno}: could not read embedded images: {type(e).__name__}: {e}")
        return out
    for idx, img in enumerate(images):
        if len(out) >= MAX_IMAGES_PER_DOCUMENT:
            warnings.append(f"page {pno}: more than {MAX_IMAGES_PER_DOCUMENT} images in this document; the rest were skipped")
            break
        data = img.data
        if not data or len(data) > MAX_GRAPHIC_BYTES:
            warnings.append(
                f"page {pno} image {idx}: {'empty' if not data else f'{len(data)} bytes exceeds the {MAX_GRAPHIC_BYTES}-byte limit'}; skipped"
            )
            continue
        fmt = (getattr(img.image, "format", None) or "").upper() if getattr(img, "image", None) else ""
        content_type = {"PNG": "image/png", "JPEG": "image/jpeg", "JPG": "image/jpeg"}.get(fmt)
        if not content_type:
            warnings.append(f"page {pno} image {idx}: unsupported embedded image format {fmt or 'unknown'!r}; skipped")
            continue
        out.append(ExtractedImage(page=pno, index=idx, data=data, content_type=content_type, sha256=hashlib.sha256(data).hexdigest()))
    return out


def parse_pdf(content: bytes) -> ParsedDocument:
    reader = PdfReader(io.BytesIO(content))
    blocks: list[Block] = []
    images: list[ExtractedImage] = []
    warnings: list[str] = []
    heading_path: list[str] = []
    title = None
    for pno, page in enumerate(reader.pages, start=1):
        images.extend(_extract_page_images(page, pno, warnings))
        text = page.extract_text() or ""
        if not text.strip():
            warnings.append(f"page {pno} has no text layer; OCR required")
            blocks.append(Block("ocr", "", {"page": pno}, page=pno))
            continue
        for lno, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            loc = {"page": pno, "line": lno}
            if title is None:
                title = line
            if line.lower().startswith("phase") and "dose table" not in line.lower():
                heading_path = [line]
                blocks.append(Block("heading", line, loc, level=2, page=pno))
            elif "|" in line:
                blocks.append(
                    Block(
                        "table",
                        "",
                        loc,
                        heading_path=list(heading_path),
                        rows=[[c.strip() for c in line.split("|")]],
                        page=pno,
                    )
                )
            else:
                blocks.append(Block("paragraph", line, loc, heading_path=list(heading_path), page=pno))
    return ParsedDocument(blocks, title, None, warnings, kind="pdf", images=images)


class OCREngine(Protocol):
    def recognize(self, content: bytes) -> dict[str, Any]: ...


class FixtureOCR:
    """OCR stub: reads precomputed blocks from a sidecar `.ocr.json` (ADR-0006). Real engines plug in here."""

    def recognize(self, content: bytes) -> dict[str, Any]:
        return json.loads(content.decode("utf-8"))


AMBIGUITY_THRESHOLD = 0.8


def parse_ocr(content: bytes, engine: OCREngine | None = None) -> ParsedDocument:
    engine = engine or FixtureOCR()
    data = engine.recognize(content)
    blocks: list[Block] = []
    warnings: list[str] = []
    for page in data.get("pages", []):
        for blk in page.get("blocks", []):
            conf = blk.get("confidence")
            alts = blk.get("alternatives")
            b = Block(
                "ocr",
                blk["text"],
                {"page": page["page"], "bbox": blk.get("bbox")},
                confidence=conf,
                alternatives=alts,
                page=page["page"],
            )
            if (conf is not None and conf < AMBIGUITY_THRESHOLD) or alts:
                warnings.append(f"ambiguous OCR at page {page['page']} bbox {blk.get('bbox')}: {blk['text']!r} alternatives={alts}")
            blocks.append(b)
    return ParsedDocument(blocks, None, None, warnings, kind="scanned_pdf")


def parse(content: bytes, content_type: str, source_type: str) -> ParsedDocument:
    if source_type == "scanned_pdf" or content_type == "application/json":
        return parse_ocr(content)
    if content_type.startswith("text/html"):
        return parse_html(content)
    if content_type == "application/pdf":
        doc = parse_pdf(content)
        if any(b.kind == "ocr" for b in doc.blocks):
            doc.warnings.append("scanned pages present; route through OCR")
        return doc
    raise ValueError(f"unsupported content type {content_type}")
