"""Export the OpenAPI document to packages/contracts/openapi.json (committed; a test asserts it is current)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://postgres@127.0.0.1:55432/moveai")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from moveai_api.main import app  # noqa: E402

OUT = ROOT / "packages" / "contracts" / "openapi.json"
spec = app.openapi()
text = json.dumps(spec, indent=2, sort_keys=True) + "\n"
if "--check" in sys.argv:
    if OUT.read_text() != text:
        sys.exit("openapi.json is stale; run `make openapi`")
    print("openapi.json is current")
else:
    OUT.write_text(text)
    print(f"wrote {OUT} ({len(spec['paths'])} paths)")
