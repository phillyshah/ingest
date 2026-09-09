from __future__ import annotations

import uuid
from typing import Any


def clean(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in r.items()}


def clean_all(rows: list[dict]) -> list[dict]:
    return [clean(r) for r in rows]  # type: ignore[misc]


def as_uuid(s: str, what: str = "id") -> uuid.UUID:
    from fastapi import HTTPException

    try:
        return uuid.UUID(s)
    except ValueError as e:
        raise HTTPException(422, {"code": "bad_id", "message": f"{what} is not a UUID"}) from e


def paginate(items: list[Any], limit: int, key: str = "id") -> dict[str, Any]:
    page = items[:limit]
    return {
        "items": page,
        "next_cursor": (page[-1][key] if len(items) > limit else None),
        "total": len(items),
    }
