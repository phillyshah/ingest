"""Fetch stage: allowlist, SSRF defence, redirect limits, size limits, hashing (spec §5)."""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import FETCH_TIMEOUT_S, MAX_DOCUMENT_BYTES, MAX_REDIRECTS, allowed_domains, allowed_file_roots


class FetchError(Exception):
    def __init__(self, error_class: str, message: str):
        super().__init__(message)
        self.error_class = error_class


@dataclass
class Fetched:
    final_url: str
    content: bytes
    content_type: str
    sha256: str
    etag: str | None = None
    last_modified: str | None = None
    warnings: list[str] = field(default_factory=list)


EXECUTABLE_MAGIC = (
    b"MZ",
    b"\x7fELF",
    b"#!",
    b"\xca\xfe\xba\xbe",
    b"PK\x03\x04",
)  # PE, ELF, script, Mach-O, zip/office


def canonicalize(url: str) -> str:
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https", "file"):
        raise FetchError("not_allowlisted", f"unsupported scheme {p.scheme!r}")
    netloc = p.netloc.lower()
    path = p.path or "/"
    return p._replace(scheme=p.scheme.lower(), netloc=netloc, path=path, fragment="").geturl()


def _is_private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise FetchError("fetch_failed", f"dns failure for {host}: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return True
    return False


def _check_url_allowed(url: str, extra_domains: set[str] | None = None) -> None:
    p = urlparse(url)
    if p.scheme == "file":
        return
    host = (p.hostname or "").lower()
    # Two allowlists, unioned. The environment variable is an operator escape hatch for a one-off; `extra_domains`
    # is the curated `source_policy` table, which is the path that scales and the only one that carries terms a
    # rights reviewer has signed. Neither weakens the SSRF check below.
    if host not in allowed_domains() | (extra_domains or set()):
        raise FetchError("not_allowlisted", f"domain {host!r} is not allowlisted")
    if _is_private(host):
        raise FetchError("ssrf_blocked", f"{host} resolves to a private/loopback address")


def _quarantine_check(content: bytes, content_type: str) -> None:
    head = content[:4]
    if any(head.startswith(m) for m in EXECUTABLE_MAGIC) and not content_type.startswith("application/pdf"):
        raise FetchError("quarantined", "unexpected executable/archive signature")
    if content_type.startswith("application/pdf") and not content.startswith(b"%PDF"):
        raise FetchError("quarantined", "content-type says PDF but bytes do not")


def fetch_file(url: str) -> Fetched:
    p = urlparse(url)
    path = Path(p.path).resolve() if p.path.startswith("/") else (Path.cwd() / (p.netloc + p.path)).resolve()
    if not any(str(path).startswith(str(root) + "/") for root in allowed_file_roots()):
        raise FetchError("not_allowlisted", f"{path} is outside allowed file roots")
    if not path.exists():
        raise FetchError("fetch_failed", f"{path} does not exist")
    content = path.read_bytes()
    if len(content) > MAX_DOCUMENT_BYTES:
        raise FetchError("too_large", f"{len(content)} bytes exceeds limit")
    ct = {
        ".html": "text/html",
        ".htm": "text/html",
        ".pdf": "application/pdf",
        ".json": "application/json",
    }.get(path.suffix.lower(), "application/octet-stream")
    _quarantine_check(content, ct)
    return Fetched(final_url=url, content=content, content_type=ct, sha256=hashlib.sha256(content).hexdigest())


def fetch_http(url: str, extra_domains: set[str] | None = None) -> Fetched:
    _check_url_allowed(url, extra_domains)
    warnings: list[str] = []
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        with httpx.Client(
            follow_redirects=False,
            timeout=FETCH_TIMEOUT_S,
            headers={"User-Agent": "MoveAI-Ingest/0.1 (+policy: allowlist only)"},
        ) as client:
            with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    nxt = resp.headers.get("location")
                    if not nxt:
                        raise FetchError("fetch_failed", "redirect without location")
                    current = httpx.URL(current).join(nxt).__str__()
                    _check_url_allowed(current, extra_domains)  # the redirect target must itself be allowlisted and non-private
                    warnings.append(f"redirected to {current}")
                    continue
                if resp.status_code != 200:
                    raise FetchError("fetch_failed", f"HTTP {resp.status_code}")
                declared = int(resp.headers.get("content-length") or 0)
                if declared > MAX_DOCUMENT_BYTES:
                    raise FetchError("too_large", f"declared {declared} bytes exceeds limit")
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_DOCUMENT_BYTES:  # defends against decompression bombs / lying content-length
                        raise FetchError("too_large", "stream exceeded size limit")
                content = bytes(buf)
                ct = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
                _quarantine_check(content, ct)
                return Fetched(
                    final_url=current,
                    content=content,
                    content_type=ct,
                    sha256=hashlib.sha256(content).hexdigest(),
                    etag=resp.headers.get("etag"),
                    last_modified=resp.headers.get("last-modified"),
                    warnings=warnings,
                )
    raise FetchError("fetch_failed", "too many redirects")


def fetch(url: str, extra_domains: set[str] | None = None) -> Fetched:
    url = canonicalize(url)
    if url.startswith("file:"):
        return fetch_file(url)
    return fetch_http(url, extra_domains)
