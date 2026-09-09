"""Parse stage: structure (headings, tables, anchors, lists, pages) with phase associations preserved (spec §5.4)."""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from pypdf import PdfReader
from selectolax.parser import HTMLParser


@dataclass
class Block:
    kind: str                      # heading | paragraph | list | table | image | footnote | ocr
    text: str
    locator: dict[str, Any]
    level: int | None = None
    heading_path: list[str] = field(default_factory=list)   # enclosing headings at the time of the block
    rows: list[list[str]] | None = None
    caption: str | None = None
    confidence: float | None = None
    alternatives: list[str] | None = None
    page: int | None = None


@dataclass
class ParsedDocument:
    blocks: list[Block]
    title: str | None
    declared_publication_date: str | None
    warnings: list[str] = field(default_factory=list)
    kind: str = "html"

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
    for node in body.iter():
        tag = node.tag
        if tag in ("script", "style"):
            continue
        if tag in ("h1", "h2", "h3", "h4"):
            level = int(tag[1])
            text = node.text(strip=True)
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, text))
            blocks.append(Block("heading", text, {"anchor": f"#{node.attributes.get('id')}" if node.attributes.get("id") else None, "css": tag},
                                level=level, heading_path=[h[1] for h in heading_stack[:-1]]))
        elif tag == "p":
            blocks.append(Block("footnote" if "footnote" in (node.attributes.get("class") or "") else "paragraph",
                                node.text(strip=True), {"css": "p"}, heading_path=[h[1] for h in heading_stack]))
        elif tag in ("ol", "ul"):
            items = [li.text(strip=True) for li in node.css("li")]
            blocks.append(Block("list", "\n".join(items), {"css": tag}, heading_path=[h[1] for h in heading_stack]))
        elif tag == "table":
            rows = [[c.text(strip=True) for c in tr.css("th,td")] for tr in node.css("tr")]
            cap = node.css_first("caption")
            blocks.append(Block("table", cap.text(strip=True) if cap else "", {"table": f"#{node.attributes.get('id')}" if node.attributes.get("id") else None},
                                heading_path=[h[1] for h in heading_stack], rows=rows, caption=cap.text(strip=True) if cap else None))
        elif tag == "img":
            blocks.append(Block("image", node.attributes.get("alt") or "", {"anchor": f"#{node.attributes.get('id')}" if node.attributes.get("id") else None,
                                                                              "src": node.attributes.get("src")}, heading_path=[h[1] for h in heading_stack]))
    date = None
    for b in blocks:
        if b.kind == "paragraph" and "Published:" in b.text:
            date = b.text.split("Published:")[1].strip().split()[0].rstrip(".")
    return ParsedDocument(blocks, title, date, kind="html")


def parse_pdf(content: bytes) -> ParsedDocument:
    reader = PdfReader(io.BytesIO(content))
    blocks: list[Block] = []
    warnings: list[str] = []
    heading_path: list[str] = []
    title = None
    for pno, page in enumerate(reader.pages, start=1):
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
                blocks.append(Block("table", "", loc, heading_path=list(heading_path), rows=[[c.strip() for c in line.split("|")]], page=pno))
            else:
                blocks.append(Block("paragraph", line, loc, heading_path=list(heading_path), page=pno))
    return ParsedDocument(blocks, title, None, warnings, kind="pdf")


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
            b = Block("ocr", blk["text"], {"page": page["page"], "bbox": blk.get("bbox")}, confidence=conf, alternatives=alts, page=page["page"])
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
