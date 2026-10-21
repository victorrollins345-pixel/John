"""On-page SEO audit (like SEMrush's On Page SEO Checker / Site Audit for one page)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from ..utils import Page, esc, fetch_ok, fmt_num, host_matches, short

PASS, WARN, FAIL, INFO = "pass", "warn", "fail", "info"
ICON = {PASS: "✅", WARN: "⚠️", FAIL: "❌", INFO: "ℹ️"}
NOISE_TAGS = ["script", "style", "noscript", "template", "svg", "iframe", "head"]


@dataclass
class Check:
    status: str
    label: str
    detail: str = ""


@dataclass
class Audit:
    url: str
    checks: list[Check] = field(default_factory=list)
    data: dict = field(default_factory=dict)

    @property
    def score(self) -> int:
        scored = [c for c in self.checks if c.status != INFO]
        if not scored:
            return 0
        points = sum(1 if c.status == PASS else 0.5 if c.status == WARN else 0 for c in scored)
        return round(100 * points / len(scored))

    def add(self, status: str, label: str, detail: str = "") -> None:
        self.checks.append(Check(status, label, detail))


def visible_text(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "lxml")
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def _meta(soup: BeautifulSoup, *, name: str | None = None, prop: str | None = None) -> str:
    if name:
        tag = soup.find("meta", attrs={"name": re.compile(f"^{re.escape(name)}$", re.I)})
    else:
        tag = soup.find("meta", attrs={"property": re.compile(f"^{re.escape(prop)}$", re.I)})
    return (tag.get("content") or "").strip() if tag else ""


def _link_rel(soup: BeautifulSoup, rel: str):
    return soup.find_all("link", rel=lambda v: v and rel in [x.lower() for x in (v if isinstance(v, list) else [v])])


def extract(page: Page) -> dict:
    soup = BeautifulSoup(page.text, "lxml")
    site = urlparse(page.url).hostname or ""
    site = site[4:] if site.startswith("www.") else site

    canon_tags = _link_rel(soup, "canonical")
    links_internal, links_external, nofollow = set(), set(), 0
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urldefrag(urljoin(page.url, href))[0]
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        if "nofollow" in [r.lower() for r in (a.get("rel") or [])]:
            nofollow += 1
        (links_internal if host_matches(urlparse(absolute).hostname, site) else links_external).add(absolute)

    images = soup.find_all("img")
    schema_types = []
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            payload = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            schema_types.append("invalid JSON-LD")
            continue
        items = payload if isinstance(payload, list) else payload.get("@graph", [payload])
        for item in items if isinstance(items, list) else [items]:
            if isinstance(item, dict) and item.get("@type"):
                t = item["@type"]
                schema_types.extend(t if isinstance(t, list) else [t])

    headings = {f"h{i}": [h.get_text(" ", strip=True) for h in soup.find_all(f"h{i}")] for i in range(1, 7)}
    text = visible_text(page.text)
    robots_meta = _meta(soup, name="robots") + " " + _meta(soup, name="googlebot")
    html_tag = soup.find("html")

    return {
        "title": soup.title.get_text(" ", strip=True) if soup.title else "",
        "description": _meta(soup, name="description"),
        "canonical": urljoin(page.url, canon_tags[0].get("href", "")) if canon_tags else "",
        "robots": robots_meta.strip(),
        "x_robots": page.headers.get("x-robots-tag", ""),
        "viewport": _meta(soup, name="viewport"),
        "lang": html_tag.get("lang", "") if html_tag else "",
        "og_title": _meta(soup, prop="og:title"),
        "og_description": _meta(soup, prop="og:description"),
        "og_image": _meta(soup, prop="og:image"),
        "twitter_card": _meta(soup, name="twitter:card"),
        "headings": headings,
        "images": len(images),
        "images_no_alt": sum(1 for i in images if not (i.get("alt") or "").strip()),
        "internal_links": len(links_internal),
        "external_links": len(links_external),
        "nofollow_links": nofollow,
        "schema": sorted(set(map(str, schema_types))),
        "hreflang": len([l for l in _link_rel(soup, "alternate") if l.get("hreflang")]),
        "favicon": bool(_link_rel(soup, "icon") or _link_rel(soup, "shortcut")),
        "word_count": len(re.findall(r"\w+", text)),
        "text_ratio": round(100 * len(text) / max(len(page.text), 1), 1),
        "html_kb": round(page.size / 1024, 1),
        "response_s": round(page.elapsed, 2),
        "compression": page.headers.get("content-encoding", ""),
        "status": page.status,
        "redirects": len(page.redirects),
        "https": page.url.startswith("https://"),
    }


def audit_page(page: Page) -> Audit:
    d = extract(page)
    a = Audit(url=page.url, data=d)

    a.add(PASS if d["status"] == 200 else FAIL, "HTTP status", str(d["status"]))
    a.add(PASS if d["https"] else FAIL, "HTTPS", "secure" if d["https"] else "page is served over HTTP")
    a.add(PASS if d["redirects"] <= 1 else WARN, "Redirects", f"{d['redirects']} hop(s)")

    t = len(d["title"])
    a.add(FAIL if not t else PASS if 30 <= t <= 60 else WARN, "Title tag",
          f"{t} chars (ideal 30–60)" if t else "missing")
    m = len(d["description"])
    a.add(FAIL if not m else PASS if 70 <= m <= 160 else WARN, "Meta description",
          f"{m} chars (ideal 70–160)" if m else "missing")

    h1 = len(d["headings"]["h1"])
    a.add(PASS if h1 == 1 else FAIL if h1 == 0 else WARN, "H1 heading",
          "exactly one" if h1 == 1 else "missing" if h1 == 0 else f"{h1} H1 tags (use one)")
    a.add(PASS if d["headings"]["h2"] else WARN, "H2 subheadings", f"{len(d['headings']['h2'])} found")

    noindex = "noindex" in (d["robots"] + " " + d["x_robots"]).lower()
    a.add(FAIL if noindex else PASS, "Indexable", "blocked by noindex" if noindex else "no noindex found")
    if d["canonical"]:
        same = urldefrag(d["canonical"])[0].rstrip("/") == page.url.rstrip("/")
        a.add(PASS if same else WARN, "Canonical", "self-referencing" if same else f"points to {short(d['canonical'], 60)}")
    else:
        a.add(WARN, "Canonical", "missing")

    a.add(PASS if d["viewport"] else FAIL, "Mobile viewport", "set" if d["viewport"] else "missing")
    a.add(PASS if d["lang"] else WARN, "HTML lang", d["lang"] or "missing")
    og = sum(bool(d[k]) for k in ("og_title", "og_description", "og_image"))
    a.add(PASS if og == 3 else WARN, "Open Graph tags", f"{og}/3 (title, description, image)")
    a.add(PASS if d["schema"] else WARN, "Structured data",
          ", ".join(d["schema"][:6]) if d["schema"] else "no JSON-LD found")

    if d["images"]:
        a.add(PASS if not d["images_no_alt"] else WARN, "Image alt text",
              f"{d['images_no_alt']} of {d['images']} images missing alt")
    else:
        a.add(INFO, "Images", "none found")
    a.add(PASS if d["word_count"] >= 300 else WARN, "Content length", f"{d['word_count']:,} words")
    a.add(PASS if d["internal_links"] >= 3 else WARN, "Internal links", str(d["internal_links"]))
    a.add(PASS if d["favicon"] else WARN, "Favicon", "found" if d["favicon"] else "missing")

    rt = d["response_s"]
    a.add(PASS if rt < 1 else WARN if rt < 3 else FAIL, "Server response", f"{rt}s")
    a.add(PASS if d["html_kb"] < 500 else WARN, "HTML size", f"{d['html_kb']} KB")
    a.add(PASS if d["compression"] else WARN, "Compression", d["compression"] or "none (enable gzip/brotli)")
    return a


def score_badge(score: int) -> str:
    return "🟢" if score >= 80 else "🟡" if score >= 60 else "🔴"


def format_audit(a: Audit) -> str:
    d = a.data
    lines = [
        f"<b>🔍 SEO Audit</b> — {esc(short(a.url, 80))}",
        f"{score_badge(a.score)} <b>Score: {a.score}/100</b>",
        "",
        f"<b>Title:</b> {esc(short(d['title'], 120)) or '—'}",
        f"<b>Description:</b> {esc(short(d['description'], 200)) or '—'}",
        "",
    ]
    order = {FAIL: 0, WARN: 1, PASS: 2, INFO: 3}
    for c in sorted(a.checks, key=lambda c: order[c.status]):
        lines.append(f"{ICON[c.status]} <b>{esc(c.label)}</b>: {esc(c.detail)}")
    lines += [
        "",
        f"🔗 Links: {d['internal_links']} internal · {d['external_links']} external · {d['nofollow_links']} nofollow",
        f"🖼 Images: {d['images']} · Text/HTML ratio: {d['text_ratio']}%",
        "",
        "<i>Next: /speed, /headings, /density, /brokenlinks for this page.</i>",
    ]
    return "\n".join(lines)


async def audit_report(url: str) -> str:
    return format_audit(audit_page(await fetch_ok(url)))


async def meta_report(url: str) -> str:
    page = await fetch_ok(url)
    d = extract(page)
    rows = [
        ("Title", d["title"]), ("Description", d["description"]), ("Canonical", d["canonical"]),
        ("Robots", d["robots"] or d["x_robots"]), ("Viewport", d["viewport"]), ("Lang", d["lang"]),
        ("og:title", d["og_title"]), ("og:description", d["og_description"]),
        ("og:image", d["og_image"]), ("twitter:card", d["twitter_card"]),
        ("hreflang tags", d["hreflang"] or ""), ("Schema types", ", ".join(d["schema"])),
    ]
    lines = [f"<b>🏷 Meta tags</b> — {esc(short(page.url, 80))}", ""]
    lines += [f"<b>{esc(k)}:</b> {esc(short(v, 220)) if v else '—'}" for k, v in rows]
    return "\n".join(lines)


async def headings_report(url: str) -> str:
    page = await fetch_ok(url)
    soup = BeautifulSoup(page.text, "lxml")
    tags = soup.find_all(re.compile(r"^h[1-6]$"))
    counts = {f"h{i}": 0 for i in range(1, 7)}
    lines = [f"<b>📑 Heading structure</b> — {esc(short(page.url, 80))}", ""]
    for tag in tags:
        counts[tag.name] += 1
    lines.append(" · ".join(f"{k.upper()}: {v}" for k, v in counts.items()))
    lines.append("")
    for tag in tags[:60]:
        level = int(tag.name[1])
        text = tag.get_text(" ", strip=True) or "(empty)"
        lines.append(f"{'  ' * (level - 1)}<code>{tag.name.upper()}</code> {esc(short(text, 90))}")
    if len(tags) > 60:
        lines.append(f"… and {len(tags) - 60} more")
    return "\n".join(lines)


async def compare_report(url_a: str, url_b: str) -> str:
    import asyncio

    pa, pb = await asyncio.gather(fetch_ok(url_a), fetch_ok(url_b))
    a, b = audit_page(pa), audit_page(pb)
    da, db = a.data, b.data
    rows = [
        ("Score", a.score, b.score),
        ("Words", da["word_count"], db["word_count"]),
        ("Title length", len(da["title"]), len(db["title"])),
        ("Description length", len(da["description"]), len(db["description"])),
        ("H1 / H2", f"{len(da['headings']['h1'])}/{len(da['headings']['h2'])}",
         f"{len(db['headings']['h1'])}/{len(db['headings']['h2'])}"),
        ("Internal links", da["internal_links"], db["internal_links"]),
        ("External links", da["external_links"], db["external_links"]),
        ("Images (no alt)", f"{da['images']} ({da['images_no_alt']})", f"{db['images']} ({db['images_no_alt']})"),
        ("Schema", len(da["schema"]), len(db["schema"])),
        ("Response (s)", da["response_s"], db["response_s"]),
        ("HTML (KB)", da["html_kb"], db["html_kb"]),
    ]
    lines = [
        "<b>⚖️ Page comparison</b>",
        f"A: {esc(short(pa.url, 70))}",
        f"B: {esc(short(pb.url, 70))}",
        "",
        "<pre>" + esc(f"{'Metric':<20}{'A':>12}{'B':>12}"),
    ]
    for name, va, vb in rows:
        lines.append(esc(f"{name:<20}{str(va):>12}{str(vb):>12}"))
    lines[-1] += "</pre>"
    return "\n".join(lines)
