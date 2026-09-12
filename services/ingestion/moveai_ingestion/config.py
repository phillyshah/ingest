from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures"

PARSER_VERSION = "html-pdf-1"
SCHEMA_VERSION = "extraction-1"
PROMPT_VERSION = "extract-1"


def model_version() -> str:
    return os.environ.get("EXTRACTION_MODEL", "mock-1")


def allowed_file_roots() -> list[Path]:
    raw = os.environ.get("ALLOWED_FILE_ROOTS")
    roots = [Path(p) for p in raw.split(":")] if raw else [FIXTURES]
    return [r.resolve() for r in roots]


def allowed_domains() -> set[str]:
    raw = os.environ.get("ALLOWED_FETCH_DOMAINS", "")
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


MAX_DOCUMENT_BYTES = int(os.environ.get("MAX_DOCUMENT_BYTES", str(25 * 1024 * 1024)))
MAX_GRAPHIC_BYTES = int(os.environ.get("MAX_GRAPHIC_BYTES", str(2 * 1024 * 1024)))
MAX_IMAGES_PER_DOCUMENT = int(os.environ.get("MAX_IMAGES_PER_DOCUMENT", "200"))  # a runaway PDF is a bug, not a big document
MAX_REDIRECTS = 3
FETCH_TIMEOUT_S = float(os.environ.get("FETCH_TIMEOUT_S", "20"))
PARSE_TIMEOUT_S = float(os.environ.get("PARSE_TIMEOUT_S", "60"))
