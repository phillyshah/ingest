"""Object storage boundary: bytes never go into Postgres. LocalStorage for dev; Supabase Storage adapter later."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol


class ObjectStorage(Protocol):
    def put(self, key: str, content: bytes, content_type: str) -> str: ...
    def get(self, ref: str) -> bytes: ...


class LocalStorage:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("LOCAL_STORAGE_DIR", ".demo-out/storage")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, content: bytes, content_type: str) -> str:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return f"local://{key}"

    def get(self, ref: str) -> bytes:
        return (self.root / ref.removeprefix("local://")).read_bytes()


def content_key(sha256: str, suffix: str) -> str:
    return f"sources/{sha256[:2]}/{sha256}{suffix}"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def get_storage() -> ObjectStorage:
    return LocalStorage()
