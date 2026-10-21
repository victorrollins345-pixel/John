"""Technical SEO: PageSpeed/Core Web Vitals, robots.txt, sitemaps, broken links,
redirects, security headers, SSL, DNS and WHOIS."""
from __future__ import annotations

import asyncio
import datetime as dt
import ssl
import time
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import dns.asyncresolver
import dns.exception
import dns.resolver
import httpx
from bs4 import BeautifulSoup

from .. import config
from ..utils import (BotError, FetchError, ProviderError, assert_public_host, domain_of, esc, fetch, fetch_ok,
                     fmt_num, host_of, normalize_url, short)

# --- PageSpeed Insights / Core Web Vitals ---------------------------------------------------
PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
LAB_AUDITS = [
    ("first-contentful-paint", "First Contentful Paint"),
    ("largest-contentful-paint", "Largest Contentful Paint"),
    ("total-blocking-time", "Total Blocking Time"),
    ("cumulative-layout-shift", "Cumulative Layout Shift"),
    ("speed-index", "Speed Index"),
]
FIELD_METRICS = [
    ("LARGEST_CONTENTFUL_PAINT_MS", "LCP", lambda v: f"{v / 1000:.1f}s"),
    ("INTERACTION_TO_NEXT_PAINT", "INP", lambda v: f"{v}ms"),
    ("CUMULATIVE_LAYOUT_SHIFT_SCORE", "CLS", lambda v: f"{v / 100:.2f}"),
]
CAT_ICON = {"FAST": "🟢", "AVERAGE": "🟡", "SLOW": "🔴"}


def _score_icon(score: float | None) -> str:
    if score is None:
        return "⚪"
    return "🟢" if score >= 0.9 else "🟡" if score >= 0.5 else "🔴"


async def pagespeed_report(url: str, strategy: str = "mobile") -> str:
    url = normalize_url(url)
    params = [("url", url), ("strategy", strategy)]
    params += [("category", c) for c in ("performance", "accessibility", "best-practices", "seo")]
    if config.PAGESPEED_API_KEY:
        params.append(("key", config.PAGESPEED_API_KEY))
    try:
        async with httpx.AsyncClient(timeout=150) as client:
            resp = await client.get(PSI_URL, params=params)
    except httpx.HTTPError:
        raise ProviderError("PageSpeed Insights didn't respond in time. Try again in a minute.") from None
    if resp.status_code == 429:
        raise ProviderError("PageSpeed quota reached. Add a free PAGESPEED_API_KEY in Railway variables.")
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code != 200:
        msg = (data.get("error") or {}).get("message", f"HTTP {resp.status_code}")
        raise ProviderError(f"PageSpeed error: {short(msg, 200)}")

    lh = data.get("lighthouseResult", {})
    cats, audits = lh.get("categories", {}), lh.get("audits", {})
    lines = [f"<b>⚡ PageSpeed ({esc(strategy)})</b> — {esc(short(url, 80))}", ""]
    for key, label in (("performance", "Performance"), ("accessibility", "Accessibility"),
                       ("best-practices", "Best practices"), ("seo", "SEO")):
        score = (cats.get(key) or {}).get("score")
        shown = "—" if score is None else round(score * 100)
        lines.append(f"{_score_icon(score)} <b>{label}:</b> {shown}")

    lines += ["", "<b>Lab metrics</b>"]
    for key, label in LAB_AUDITS:
        audit = audits.get(key) or {}
        lines.append(f"{_score_icon(audit.get('score'))} {label}: {esc(audit.get('displayValue', '—'))}")

    field = (data.get("loadingExperience") or {}).get("metrics") or {}
    if field:
        overall = (data.get("loadingExperience") or {}).get("overall_category", "")
        lines += ["", f"<b>Real-user Core Web Vitals</b> {CAT_ICON.get(overall, '')} {esc(overall.title())}"]
        for key, label, fmt in FIELD_METRICS:
            m = field.get(key)
            if m and m.get("percentile") is not None:
                lines.append(f"{CAT_ICON.get(m.get('category'), '⚪')} {label}: {fmt(m['percentile'])} (p75)")
    else:
        lines += ["", "<i>No real-user (CrUX) data for this URL yet.</i>"]

    opportunities = [
        a for a in audits.values()
        if (a.get("details") or {}).get("type") == "opportunity" and (a.get("score") or 1) < 0.9
        and (a.get("details") or {}).get("overallSavingsMs", 0) > 0
    ]
    opportunities.sort(key=lambda a: a["details"]["overallSavingsMs"], reverse=True)
    if opportunities:
        lines += ["", "<b>Top opportunities</b>"]
        for a in opportunities[:6]:
            saving = a["details"]["overallSavingsMs"] / 1000
            lines.append(f"• {esc(a.get('title', ''))} — save ~{saving:.1f}s")
    other = "desktop" if strategy == "mobile" else "mobile"
    lines += ["", f"<i>Run <code>/speed {esc(url)} {other}</code> for {other}.</i>"]
    return "\n".join(lines)


# --- robots.txt ------------------------------------------------------------------------------
async def _robots_text(host: str) -> tuple[int, str, str]:
    page = await fetch(f"https://{host}/robots.txt", timeout=15)
    return page.status, page.text if page.status == 200 else "", page.url


async def robots_report(target: str) -> str:
    host = host_of(target)
    status, text, final_url = await _robots_text(host)
    lines = [f"<b>🤖 robots.txt</b> — {esc(host)}", ""]
    if status != 200 or not text.strip():
        lines.append(f"⚠️ No robots.txt found (HTTP {status}). Search engines will crawl everything.")
        lines.append("Create one with at least a <code>Sitemap:</code> line.")
        return "\n".join(lines)

    raw_lines = text.splitlines()
    rp = RobotFileParser()
    rp.parse(raw_lines)
    home = f"https://{host}/"
    sitemaps = [l.split(":", 1)[1].strip() for l in raw_lines if l.lower().startswith("sitemap:")]
    agents = {l.split(":", 1)[1].strip() for l in raw_lines if l.lower().startswith("user-agent:")}
    disallows = [l for l in raw_lines if l.lower().startswith("disallow:") and l.split(":", 1)[1].strip()]

    for bot in ("Googlebot", "Bingbot", "*"):
        ok = rp.can_fetch(bot, home)
        lines.append(f"{'✅' if ok else '❌'} {bot}: homepage {'allowed' if ok else 'BLOCKED'}")
    for bot in ("GPTBot", "ClaudeBot", "CCBot"):
        ok = rp.can_fetch(bot, home)
        lines.append(f"ℹ️ {bot} (AI crawler): {'allowed' if ok else 'blocked'}")
    lines += [
        "",
        f"User-agent groups: {len(agents)} · Disallow rules: {len(disallows)}",
        f"{'✅' if sitemaps else '⚠️'} Sitemap lines: {len(sitemaps)}",
    ]
    lines += [f"   • {esc(short(s, 90))}" for s in sitemaps[:5]]
    preview = "\n".join(raw_lines[:35])[:1800]  # keep the <pre> block inside one Telegram message
    lines += ["", f"<pre>{esc(preview)}</pre>"]
    if len(raw_lines) > 35:
        lines.append(f"<i>… {len(raw_lines) - 35} more lines</i>")
    return "\n".join(lines)


# --- Sitemaps ----------------------------------------------------------------------------------
async def _load_sitemap(url: str) -> BeautifulSoup | None:
    try:
        page = await fetch(url, timeout=25)
    except BotError:
        return None
    if page.status != 200 or ("<urlset" not in page.text and "<sitemapindex" not in page.text):
        return None
    return BeautifulSoup(page.text, "xml")


async def sitemap_report(target: str) -> str:
    host = host_of(target)
    candidates = []
    try:
        _, robots, _ = await _robots_text(host)
        candidates = [l.split(":", 1)[1].strip() for l in robots.splitlines() if l.lower().startswith("sitemap:")]
    except BotError:
        pass
    candidates += [f"https://{host}/sitemap.xml", f"https://{host}/sitemap_index.xml", f"https://{host}/wp-sitemap.xml"]

    lines = [f"<b>🗺 Sitemap</b> — {esc(host)}", ""]
    for url in dict.fromkeys(candidates):
        soup = await _load_sitemap(url)
        if soup is None:
            continue
        lines.append(f"✅ Found: {esc(url)}")
        children = [s.find("loc").get_text(strip=True) for s in soup.find_all("sitemap") if s.find("loc")]
        urls = soup.find_all("url")
        if children:
            lines.append(f"Sitemap index with {len(children)} child sitemaps")
            child_soups = await asyncio.gather(*(_load_sitemap(c) for c in children[:8]))
            total = 0
            for child, csoup in zip(children[:8], child_soups):
                n = len(csoup.find_all("url")) if csoup else None
                total += n or 0
                lines.append(f"• {esc(short(child, 80))} — {fmt_num(n) if n is not None else 'unreadable (.gz?)'} URLs")
            more = " (first 8 sitemaps)" if len(children) > 8 else ""
            lines.append(f"\n<b>Total URLs counted:</b> {total:,}{more}")
        else:
            lastmods = sorted(u.find("lastmod").get_text(strip=True) for u in urls if u.find("lastmod"))
            lines.append(f"<b>URLs:</b> {len(urls):,}")
            lines.append(f"<b>Newest lastmod:</b> {esc(lastmods[-1]) if lastmods else '—'}")
            if len(urls) > 50_000:
                lines.append("❌ Over 50,000 URLs — split it into multiple sitemaps.")
            for u in urls[:10]:
                loc = u.find("loc")
                if loc:
                    lines.append(f"• {esc(short(loc.get_text(strip=True), 90))}")
        return "\n".join(lines)
    lines.append("❌ No XML sitemap found in robots.txt or at the usual locations.")
    lines.append("Create /sitemap.xml and add <code>Sitemap: https://…/sitemap.xml</code> to robots.txt.")
    return "\n".join(lines)


# --- Broken links -----------------------------------------------------------------------------
async def broken_links_report(url: str) -> str:
    page = await fetch_ok(url)
    soup = BeautifulSoup(page.text, "lxml")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urldefrag(urljoin(page.url, href))[0]
        if urlparse(absolute).scheme in ("http", "https"):
            links.append(absolute)
    links = list(dict.fromkeys(links))[: config.BROKEN_LINKS_LIMIT]
    sem = asyncio.Semaphore(10)

    async def check(link: str):
        async with sem:
            try:
                res = await fetch(link, method="HEAD", timeout=15)
                if res.status in (400, 403, 405, 406, 501):
                    res = await fetch(link, timeout=15)
                return link, res.status, ""
            except BotError as exc:
                return link, None, str(exc)

    results = await asyncio.gather(*(check(l) for l in links))
    broken = [(l, s, e) for l, s, e in results if s is None or s >= 400]
    lines = [
        f"<b>🔗 Broken link check</b> — {esc(short(page.url, 80))}",
        f"Checked {len(results)} unique links" + (f" (limit {config.BROKEN_LINKS_LIMIT})" if len(results) >= config.BROKEN_LINKS_LIMIT else ""),
        "",
    ]
    if not broken:
        lines.append("✅ No broken links found.")
    else:
        lines.append(f"❌ <b>{len(broken)} broken</b>")
        for link, status, err in broken[:40]:
            lines.append(f"• <code>{status or 'ERR'}</code> {esc(short(link, 90))}")
    return "\n".join(lines)


# --- Redirects / canonical host ------------------------------------------------------------------
async def redirects_report(target: str) -> str:
    domain = domain_of(target)
    variants = [f"http://{domain}/", f"https://{domain}/", f"http://www.{domain}/", f"https://www.{domain}/"]

    async def trace(u: str):
        try:
            p = await fetch(u, method="GET", timeout=15)
            return u, p, ""
        except BotError as exc:
            return u, None, str(exc)

    results = await asyncio.gather(*(trace(v) for v in variants))
    finals = {p.url.rstrip("/") for _, p, _ in results if p and p.status < 400}
    lines = [f"<b>↪️ Redirect check</b> — {esc(domain)}", ""]
    for start, page, err in results:
        if not page:
            lines.append(f"⚪ {esc(start)} → {esc(short(err, 60))}")
            continue
        hops = " → ".join(f"{code}" for code, _ in page.redirects) or "no redirect"
        icon = "✅" if page.status < 400 else "❌"
        lines.append(f"{icon} {esc(start)}\n    ↳ {esc(hops)} → <b>{page.status}</b> {esc(short(page.url, 70))}")
        if any(code == 302 or code == 307 for code, _ in page.redirects):
            lines.append("    ⚠️ uses a temporary redirect — prefer 301/308")
        if len(page.redirects) > 1:
            lines.append(f"    ⚠️ redirect chain of {len(page.redirects)} hops")
    lines.append("")
    if len(finals) == 1:
        lines.append(f"✅ All versions resolve to one canonical host: {esc(finals.pop())}")
    elif finals:
        lines.append("❌ Different versions end on different URLs — pick one and 301 the others:")
        lines += [f"   • {esc(f)}" for f in sorted(finals)]
    return "\n".join(lines)


# --- Security headers ----------------------------------------------------------------------------
SECURITY_HEADERS = [
    ("strict-transport-security", "HSTS"),
    ("content-security-policy", "Content-Security-Policy"),
    ("x-content-type-options", "X-Content-Type-Options"),
    ("x-frame-options", "X-Frame-Options"),
    ("referrer-policy", "Referrer-Policy"),
    ("permissions-policy", "Permissions-Policy"),
]


async def security_report(url: str) -> str:
    page = await fetch_ok(url)
    lines = [f"<b>🛡 Security headers</b> — {esc(short(page.url, 80))}", ""]
    present = 0
    for key, label in SECURITY_HEADERS:
        value = page.headers.get(key)
        present += bool(value)
        lines.append(f"{'✅' if value else '⚠️'} <b>{label}</b>: {esc(short(value, 80)) if value else 'missing'}")
    server = page.headers.get("server") or page.headers.get("x-powered-by")
    lines += ["", f"Score: {present}/{len(SECURITY_HEADERS)}"]
    if server:
        lines.append(f"ℹ️ Server banner exposed: {esc(server)}")
    return "\n".join(lines)


# --- SSL certificate --------------------------------------------------------------------------------
async def ssl_report(target: str) -> str:
    host = host_of(target)
    await assert_public_host(host)
    ctx = ssl.create_default_context()
    lines = [f"<b>🔒 SSL certificate</b> — {esc(host)}", ""]
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, 443, ssl=ctx, server_hostname=host), 15)
    except ssl.SSLCertVerificationError as exc:
        lines.append(f"❌ Certificate is NOT valid: {esc(exc.verify_message or exc)}")
        return "\n".join(lines)
    except (OSError, asyncio.TimeoutError):
        raise FetchError(f"Could not connect to {host}:443.") from None
    try:
        cert = writer.get_extra_info("peercert") or {}
        ssl_obj = writer.get_extra_info("ssl_object")
        protocol = ssl_obj.version() if ssl_obj else "?"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass

    expires = dt.datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), dt.timezone.utc)
    issued = dt.datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notBefore"]), dt.timezone.utc)
    days_left = (expires - dt.datetime.now(dt.timezone.utc)).days
    issuer = dict(x[0] for x in cert.get("issuer", ()))
    subject = dict(x[0] for x in cert.get("subject", ()))
    sans = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
    icon = "✅" if days_left > 21 else "⚠️" if days_left > 0 else "❌"
    lines += [
        "✅ Certificate is valid and trusted",
        f"{icon} Expires: {expires:%Y-%m-%d} ({days_left} days left)",
        f"Issued: {issued:%Y-%m-%d}",
        f"Issuer: {esc(issuer.get('organizationName') or issuer.get('commonName', '?'))}",
        f"Subject: {esc(subject.get('commonName', '?'))}",
        f"Protocol: {esc(protocol)}",
        f"Covers {len(sans)} names: {esc(', '.join(sans[:8]))}{' …' if len(sans) > 8 else ''}",
    ]
    return "\n".join(lines)


# --- DNS ---------------------------------------------------------------------------------------------
async def _resolve(resolver, name: str, rtype: str) -> list[str]:
    try:
        answer = await resolver.resolve(name, rtype)
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return []
    out = []
    for r in answer:
        if rtype == "TXT":
            out.append(b"".join(r.strings).decode(errors="replace"))
        elif rtype == "MX":
            out.append(f"{r.preference} {r.exchange.to_text().rstrip('.')}")
        else:
            out.append(r.to_text().rstrip("."))
    return out


async def dns_report(target: str) -> str:
    domain = domain_of(target)
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = 10
    try:
        await resolver.resolve(domain, "NS")
    except dns.resolver.NXDOMAIN:
        raise FetchError(f"{domain} does not exist (NXDOMAIN).") from None
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        pass
    types = ["A", "AAAA", "CNAME", "MX", "NS", "TXT", "CAA"]
    results = await asyncio.gather(*(_resolve(resolver, domain, t) for t in types))
    dmarc = await _resolve(resolver, f"_dmarc.{domain}", "TXT")
    www = await _resolve(resolver, f"www.{domain}", "CNAME") or await _resolve(resolver, f"www.{domain}", "A")
    lines = [f"<b>🌐 DNS records</b> — {esc(domain)}", ""]
    records = dict(zip(types, results))
    for rtype, values in records.items():
        if values:
            lines.append(f"<b>{rtype}</b>")
            lines += [f"  • {esc(short(v, 110))}" for v in values[:8]]
    spf = [t for t in records["TXT"] if t.lower().startswith("v=spf1")]
    lines += [
        "",
        f"{'✅' if www else '⚠️'} www.{esc(domain)}: {esc(', '.join(www[:2])) if www else 'no record'}",
        f"{'✅' if spf else '⚠️'} SPF: {'present' if spf else 'missing'}",
        f"{'✅' if dmarc else '⚠️'} DMARC: {esc(short(dmarc[0], 80)) if dmarc else 'missing'}",
        f"{'✅' if records['CAA'] else 'ℹ️'} CAA: {'present' if records['CAA'] else 'none'}",
    ]
    return "\n".join(lines)


# --- WHOIS / domain age ----------------------------------------------------------------------------
def _first(value):
    if isinstance(value, list):
        value = [v for v in value if v]
        return value[0] if value else None
    return value


async def whois_report(target: str) -> str:
    import whois  # python-whois

    domain = domain_of(target)
    started = time.perf_counter()
    try:
        w = await asyncio.wait_for(asyncio.to_thread(whois.whois, domain), 30)
    except Exception as exc:  # the library raises many different types
        raise FetchError(f"WHOIS lookup failed for {domain}: {short(str(exc), 120)}") from None
    created, expires, updated = (_first(w.get(k)) for k in ("creation_date", "expiration_date", "updated_date"))
    now = dt.datetime.now()
    lines = [f"<b>📇 WHOIS</b> — {esc(domain)}", ""]
    if isinstance(created, dt.datetime):
        age_days = (now - created.replace(tzinfo=None)).days
        lines.append(f"<b>Domain age:</b> {age_days // 365} years, {(age_days % 365) // 30} months")
    lines += [
        f"<b>Registrar:</b> {esc(_first(w.get('registrar')) or '—')}",
        f"<b>Created:</b> {created:%Y-%m-%d}" if isinstance(created, dt.datetime) else "<b>Created:</b> —",
        f"<b>Expires:</b> {expires:%Y-%m-%d}" if isinstance(expires, dt.datetime) else "<b>Expires:</b> —",
        f"<b>Updated:</b> {updated:%Y-%m-%d}" if isinstance(updated, dt.datetime) else "<b>Updated:</b> —",
        f"<b>Organisation:</b> {esc(_first(w.get('org')) or '— (privacy protected)')}",
        f"<b>Country:</b> {esc(_first(w.get('country')) or '—')}",
    ]
    ns = w.get("name_servers") or []
    ns = sorted({str(n).lower() for n in (ns if isinstance(ns, list) else [ns])})
    if ns:
        lines.append(f"<b>Name servers:</b> {esc(', '.join(ns[:4]))}")
    if isinstance(expires, dt.datetime) and (expires.replace(tzinfo=None) - now).days < 30:
        lines.append("⚠️ Domain expires within 30 days — renew it!")
    lines.append(f"\n<i>Looked up in {time.perf_counter() - started:.1f}s</i>")
    return "\n".join(lines)
