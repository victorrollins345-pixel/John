"""Shared helpers: safe HTTP fetching, URL handling and Telegram text formatting."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; MiniSEOToolsBot/1.0; +https://t.me/MiniSEOToolsBot)"
MAX_HTML_CHARS = 3_000_000
TG_CHUNK = 3900  # Telegram's hard limit is 4096 characters per message


# --- Errors shown to the user as-is ------------------------------------------------
class BotError(Exception):
    """An error whose message is safe and useful to show to the user."""


class FetchError(BotError):
    pass


class NotConfigured(BotError):
    pass


class ProviderError(BotError):
    pass


# --- Text helpers ---------------------------------------------------------------------
def esc(value) -> str:
    return html.escape(str(value), quote=True)


def fmt_num(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return esc(value)
    if num.is_integer():
        return f"{int(num):,}"
    return f"{num:,.2f}"


def short(text: str, limit: int = 70) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def split_message(text: str, limit: int = TG_CHUNK) -> list[str]:
    """Split on line boundaries so HTML tags (always closed per line) stay valid."""
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # pathological single line
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return chunks or ["(empty)"]


# --- URL helpers ------------------------------------------------------------------------
def normalize_url(raw: str) -> str:
    raw = raw.strip().strip("<>\"'")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    host = urlparse(raw).hostname
    if not host or "." not in host:
        raise FetchError("That doesn't look like a valid URL or domain.")
    return raw


def host_of(raw: str) -> str:
    return (urlparse(normalize_url(raw)).hostname or "").lower().rstrip(".")


def domain_of(raw: str) -> str:
    host = host_of(raw)
    return host[4:] if host.startswith("www.") else host


def host_matches(host: str | None, domain: str) -> bool:
    if not host:
        return False
    host = host.lower().rstrip(".")
    host = host[4:] if host.startswith("www.") else host
    return host == domain or host.endswith("." + domain)


async def assert_public_host(host: str | None) -> None:
    """Refuse to fetch internal/private addresses (protects your Railway service)."""
    if not host:
        raise FetchError("Missing host name.")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchError(f"Could not resolve {host} — check the domain name.") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError("Private or internal addresses can't be checked.")


# --- Fetching ----------------------------------------------------------------------------
@dataclass
class Page:
    url: str  # final URL after redirects
    status: int
    headers: httpx.Headers
    text: str
    size: int
    elapsed: float  # seconds for the final request
    redirects: list[tuple[int, str]] = field(default_factory=list)


async def fetch(url: str, *, method: str = "GET", timeout: float = 20.0, max_redirects: int = 6) -> Page:
    url = normalize_url(url)
    redirects: list[tuple[int, str]] = []
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en;q=0.9,*;q=0.5",
    }
    try:
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=False) as client:
            for _ in range(max_redirects + 1):
                await assert_public_host(urlparse(url).hostname)
                started = time.perf_counter()
                resp = await client.request(method, url)
                elapsed = time.perf_counter() - started
                location = resp.headers.get("location")
                if resp.is_redirect and location:
                    redirects.append((resp.status_code, url))
                    url = urljoin(url, location)
                    if urlparse(url).scheme not in ("http", "https"):
                        raise FetchError("Redirected to an unsupported address.")
                    continue
                text = resp.text[:MAX_HTML_CHARS] if method != "HEAD" else ""
                return Page(
                    url=url,
                    status=resp.status_code,
                    headers=resp.headers,
                    text=text,
                    size=len(resp.content),
                    elapsed=elapsed,
                    redirects=redirects,
                )
    except httpx.TimeoutException:
        raise FetchError(f"Timed out while loading {url}") from None
    except httpx.HTTPError as exc:
        raise FetchError(f"Could not load {url} ({exc.__class__.__name__})") from None
    raise FetchError("Too many redirects.")


async def fetch_ok(url: str, **kwargs) -> Page:
    page = await fetch(url, **kwargs)
    if page.status >= 400:
        raise FetchError(f"{page.url} returned HTTP {page.status}.")
    return page
