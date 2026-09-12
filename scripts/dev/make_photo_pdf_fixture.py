"""Regenerate fixtures/permitted-sources/owned_demo_protocol_with_photo.pdf.

Not run as part of the test suite or CI: it needs `reportlab` and `Pillow`, which are not project dependencies —
they happened to be present when this fixture was first built and are only needed again if the fixture itself
needs to change. Run by hand:

    uv run --with reportlab --with pillow python scripts/dev/make_photo_pdf_fixture.py
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parents[2] / "fixtures" / "permitted-sources" / "owned_demo_protocol_with_photo.pdf"


def main() -> None:
    photo = Image.new("RGB", (120, 80), color=(200, 30, 30))
    photo_path = "/tmp/_fixture_photo.png"
    photo.save(photo_path)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 720, "Phase 1 - Demo phase")
    c.drawString(72, 700, "Heel slide (active)")
    c.drawString(72, 680, "Step one. Step two.")
    c.drawImage(photo_path, 72, 550, width=120, height=80)
    c.showPage()
    c.save()

    OUT.write_bytes(buf.getvalue())
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
