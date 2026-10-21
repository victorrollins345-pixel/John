"""Google SERP results and rank checks (DataForSEO, or Serper.dev as a cheaper fallback)."""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

from .. import config
from ..utils import NotConfigured, ProviderError, domain_of, esc, host_matches, short
from .dataforseo import dfs_post

NEEDS_SERP = (
    "Rank checking needs a SERP data provider.\n"
    "Add DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD, or a SERPER_API_KEY (serper.dev) in Railway → Variables."
)


async def get_serp(keyword: str, depth: int = 100) -> list[dict]:
    """Return organic results as [{position, url, domain, title}]."""
    if config.dataforseo_enabled():
        result = await dfs_post("serp/google/organic/live/advanced", [{
            "keyword": keyword, "location_code": config.DFS_LOCATION_CODE,
            "language_code": config.DFS_LANGUAGE_CODE, "depth": depth,
        }])
        items = (result[0].get("items") if result else None) or []
        return [
            {"position": i.get("rank_group"), "url": i.get("url", ""), "domain": i.get("domain", ""),
             "title": i.get("title", "")}
            for i in items if i.get("type") == "organic"
        ]
    if config.SERPER_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": config.SERPER_API_KEY, "Content-Type": "application/json"},
                    json={"q": keyword, "gl": config.SERPER_GL, "hl": config.SERPER_HL, "num": depth},
                )
        except httpx.HTTPError:
            raise ProviderError("Serper didn't respond. Please try again.") from None
        if resp.status_code != 200:
            raise ProviderError(f"Serper HTTP {resp.status_code} — check SERPER_API_KEY / credits.")
        return [
            {"position": o.get("position"), "url": o.get("link", ""),
             "domain": urlparse(o.get("link", "")).hostname or "", "title": o.get("title", "")}
            for o in resp.json().get("organic", [])
        ]
    raise NotConfigured(NEEDS_SERP)


async def find_rank(domain: str, keyword: str) -> dict | None:
    domain = domain_of(domain)
    for item in await get_serp(keyword):
        if host_matches(urlparse(item["url"]).hostname or item["domain"], domain):
            return item
    return None


async def serp_report(keyword: str) -> str:
    results = await get_serp(keyword, depth=20)
    lines = [f"<b>🔎 Google top results</b> for “{esc(keyword)}”", ""]
    if not results:
        lines.append("No organic results returned.")
    for r in results[:20]:
        lines.append(f"<b>{r['position']}.</b> {esc(short(r['title'], 70))}")
        lines.append(f"     <i>{esc(short(r['url'], 80))}</i>")
    return "\n".join(lines)


async def rank_report(domain: str, keyword: str) -> str:
    domain = domain_of(domain)
    hit = await find_rank(domain, keyword)
    if not hit:
        return f"📉 <b>{esc(domain)}</b> is not in the top 100 for “{esc(keyword)}”."
    return (
        f"🎯 <b>{esc(domain)}</b> ranks <b>#{hit['position']}</b> for “{esc(keyword)}”\n"
        f"URL: {esc(short(hit['url'], 90))}\n\n"
        f"<i>Track it daily: /track {esc(domain)} {esc(keyword)}</i>"
    )
