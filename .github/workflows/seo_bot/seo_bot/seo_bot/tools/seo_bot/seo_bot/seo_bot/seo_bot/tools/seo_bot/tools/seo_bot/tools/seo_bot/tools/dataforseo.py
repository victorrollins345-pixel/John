"""SEMrush-style data features powered by the DataForSEO API (pay-as-you-go).

Domain overview, organic keywords, competitors, backlinks, keyword volume and keyword ideas.
Docs: https://docs.dataforseo.com/v3/
"""
from __future__ import annotations

import httpx

from .. import config
from ..utils import NotConfigured, ProviderError, domain_of, esc, fmt_num, short

API = "https://api.dataforseo.com/v3/"
NEEDS_DFS = (
    "This command needs DataForSEO (the data source behind keyword volumes, backlinks and traffic).\n"
    "Add DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD in Railway → Variables."
)


async def dfs_post(path: str, payload: list[dict]) -> list:
    if not config.dataforseo_enabled():
        raise NotConfigured(NEEDS_DFS)
    try:
        async with httpx.AsyncClient(timeout=120, auth=(config.DFS_LOGIN, config.DFS_PASSWORD)) as client:
            resp = await client.post(API + path, json=payload)
    except httpx.HTTPError:
        raise ProviderError("DataForSEO didn't respond. Please try again.") from None
    if resp.status_code == 401:
        raise ProviderError("DataForSEO rejected the login — check DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD.")
    if resp.status_code != 200:
        raise ProviderError(f"DataForSEO HTTP {resp.status_code}")
    data = resp.json()
    if data.get("status_code") != 20000:
        raise ProviderError(f"DataForSEO: {data.get('status_message', 'unknown error')}")
    task = (data.get("tasks") or [{}])[0]
    if task.get("status_code") != 20000:
        raise ProviderError(f"DataForSEO: {task.get('status_message', 'unknown error')}")
    return task.get("result") or []


def _loc() -> dict:
    return {"location_code": config.DFS_LOCATION_CODE, "language_code": config.DFS_LANGUAGE_CODE}


def _market() -> str:
    return f"<i>Market: location {config.DFS_LOCATION_CODE}, language “{esc(config.DFS_LANGUAGE_CODE)}”</i>"


# --- Domain overview -----------------------------------------------------------------------
async def overview_report(target: str) -> str:
    domain = domain_of(target)
    result = await dfs_post("dataforseo_labs/google/domain_rank_overview/live", [{"target": domain, **_loc()}])
    items = (result[0].get("items") if result else None) or []
    if not items:
        return f"<b>📈 Domain overview</b> — {esc(domain)}\n\nNo organic data found for this market."
    m = (items[0].get("metrics") or {})
    org, paid = m.get("organic") or {}, m.get("paid") or {}
    top3 = (org.get("pos_1") or 0) + (org.get("pos_2_3") or 0)
    lines = [
        f"<b>📈 Domain overview</b> — {esc(domain)}",
        _market(),
        "",
        "<b>Organic search</b>",
        f"• Est. monthly traffic: <b>{fmt_num(round(org.get('etv') or 0))}</b>",
        f"• Ranking keywords: <b>{fmt_num(org.get('count'))}</b>",
        f"• Traffic value: ${fmt_num(round(org.get('estimated_paid_traffic_cost') or 0))}/mo",
        f"• Positions: Top 3: {fmt_num(top3)} · 4–10: {fmt_num(org.get('pos_4_10'))} · "
        f"11–20: {fmt_num(org.get('pos_11_20'))} · 21–50: {fmt_num((org.get('pos_21_30') or 0) + (org.get('pos_31_40') or 0) + (org.get('pos_41_50') or 0))}",
        f"• Trend: 🆕 {fmt_num(org.get('is_new'))} new · ⬆️ {fmt_num(org.get('is_up'))} up · "
        f"⬇️ {fmt_num(org.get('is_down'))} down · ❌ {fmt_num(org.get('is_lost'))} lost",
        "",
        "<b>Paid search</b>",
        f"• Paid keywords: {fmt_num(paid.get('count'))} · Est. paid traffic: {fmt_num(round(paid.get('etv') or 0))}",
        "",
        f"<i>More: /rankedkw {esc(domain)} · /competitors {esc(domain)} · /backlinks {esc(domain)}</i>",
    ]
    return "\n".join(lines)


# --- Organic keywords a domain ranks for -------------------------------------------------------
async def ranked_keywords_report(target: str, limit: int = 25) -> str:
    domain = domain_of(target)
    result = await dfs_post("dataforseo_labs/google/ranked_keywords/live", [{
        "target": domain, **_loc(), "limit": limit,
        "order_by": ["keyword_data.keyword_info.search_volume,desc"],
    }])
    r = result[0] if result else {}
    items = r.get("items") or []
    lines = [f"<b>🔑 Top organic keywords</b> — {esc(domain)}", _market(),
             f"Total ranking keywords: {fmt_num(r.get('total_count'))}", ""]
    if not items:
        lines.append("No ranking keywords found.")
    for it in items:
        kd = it.get("keyword_data") or {}
        info = kd.get("keyword_info") or {}
        serp = ((it.get("ranked_serp_element") or {}).get("serp_item") or {})
        pos = serp.get("rank_group", "?")
        lines.append(
            f"<b>#{pos}</b> {esc(kd.get('keyword', ''))} — vol {fmt_num(info.get('search_volume'))}"
            f" · traffic ~{fmt_num(round(serp.get('etv') or 0))}"
        )
        if serp.get("relative_url"):
            lines.append(f"     <i>{esc(short(serp['relative_url'], 70))}</i>")
    return "\n".join(lines)


# --- Organic competitors --------------------------------------------------------------------------
async def competitors_report(target: str, limit: int = 15) -> str:
    domain = domain_of(target)
    result = await dfs_post("dataforseo_labs/google/competitors_domain/live", [{
        "target": domain, **_loc(), "limit": limit + 1,
    }])
    items = [i for i in ((result[0].get("items") if result else None) or []) if i.get("domain") != domain]
    lines = [f"<b>🥊 Organic competitors</b> — {esc(domain)}", _market(), ""]
    if not items:
        lines.append("No competitors found.")
    for i, it in enumerate(items[:limit], 1):
        org = ((it.get("full_domain_metrics") or {}).get("organic") or {})
        lines.append(
            f"{i}. <b>{esc(it.get('domain', ''))}</b> — {fmt_num(it.get('intersections'))} common keywords"
            f" · avg pos {fmt_num(round(it.get('avg_position') or 0, 1))}"
            f" · traffic ~{fmt_num(round(org.get('etv') or 0))}"
        )
    return "\n".join(lines)


# --- Backlinks ----------------------------------------------------------------------------------------
async def backlinks_report(target: str) -> str:
    domain = domain_of(target)
    summary = await dfs_post("backlinks/summary/live", [{"target": domain, "include_subdomains": True}])
    s = summary[0] if summary else {}
    refs = await dfs_post("backlinks/referring_domains/live", [{
        "target": domain, "limit": 15, "order_by": ["rank,desc"], "exclude_internal_backlinks": True,
    }])
    ref_items = (refs[0].get("items") if refs else None) or []
    anchors = await dfs_post("backlinks/anchors/live", [{
        "target": domain, "limit": 10, "order_by": ["backlinks,desc"],
    }])
    anchor_items = (anchors[0].get("items") if anchors else None) or []

    lines = [
        f"<b>🔗 Backlink profile</b> — {esc(domain)}",
        "",
        f"• Domain rank: <b>{fmt_num(s.get('rank'))}</b> /1000",
        f"• Backlinks: <b>{fmt_num(s.get('backlinks'))}</b>",
        f"• Referring domains: <b>{fmt_num(s.get('referring_domains'))}</b>",
        f"• Referring IPs: {fmt_num(s.get('referring_ips'))} · subnets: {fmt_num(s.get('referring_subnets'))}",
        f"• Dofollow domains: {fmt_num((s.get('referring_domains') or 0) - (s.get('referring_domains_nofollow') or 0))}",
        f"• Broken backlinks: {fmt_num(s.get('broken_backlinks'))} · Spam score: {fmt_num(s.get('backlinks_spam_score'))}",
        "",
        "<b>Top referring domains</b>",
    ]
    for it in ref_items:
        lines.append(f"• {esc(it.get('domain', ''))} — rank {fmt_num(it.get('rank'))}, {fmt_num(it.get('backlinks'))} links")
    if anchor_items:
        lines += ["", "<b>Top anchor texts</b>"]
        for it in anchor_items:
            lines.append(f"• “{esc(short(it.get('anchor') or '(empty)', 60))}” — {fmt_num(it.get('backlinks'))}")
    return "\n".join(lines)


# --- Keyword volume / CPC / competition --------------------------------------------------------------
async def volume_report(keywords: list[str]) -> str:
    keywords = [k.strip().lower() for k in keywords if k.strip()][:50]
    result = await dfs_post("keywords_data/google_ads/search_volume/live", [{"keywords": keywords, **_loc()}])
    lines = ["<b>📊 Keyword volume</b>", _market(), ""]
    if not result:
        lines.append("No data returned.")
    for it in sorted(result, key=lambda x: x.get("search_volume") or 0, reverse=True):
        cpc = it.get("cpc")
        lines.append(
            f"<b>{esc(it.get('keyword', ''))}</b> — vol {fmt_num(it.get('search_volume'))}"
            f" · CPC {'$' + format(cpc, '.2f') if cpc else '—'}"
            f" · competition {esc(it.get('competition') or '—')}"
        )
    return "\n".join(lines)


# --- Keyword ideas with difficulty (Keyword Magic Tool) ---------------------------------------------
def _kd_icon(kd) -> str:
    if kd is None:
        return "⚪"
    return "🟢" if kd < 30 else "🟡" if kd < 60 else "🔴"


async def ideas_report(seed: str, limit: int = 30) -> str:
    seed = seed.strip().lower()
    result = await dfs_post("dataforseo_labs/google/keyword_suggestions/live", [{
        "keyword": seed, **_loc(), "limit": limit, "include_seed_keyword": True,
        "order_by": ["keyword_info.search_volume,desc"],
    }])
    r = result[0] if result else {}
    items = list(r.get("items") or [])
    seed_data = r.get("seed_keyword_data")
    lines = [f"<b>🧲 Keyword ideas</b> for “{esc(seed)}”", _market(),
             f"Total ideas available: {fmt_num(r.get('total_count'))}", ""]
    if seed_data:
        items.insert(0, seed_data)
    if not items:
        lines.append("No ideas found. Try /suggest for free autocomplete ideas.")
    for it in items:
        info = it.get("keyword_info") or {}
        kd = (it.get("keyword_properties") or {}).get("keyword_difficulty")
        intent = ((it.get("search_intent_info") or {}).get("main_intent") or "")[:4]
        cpc = info.get("cpc")
        lines.append(
            f"{_kd_icon(kd)} <b>{esc(it.get('keyword', ''))}</b> — vol {fmt_num(info.get('search_volume'))}"
            f" · KD {fmt_num(kd)} · CPC {'$' + format(cpc, '.2f') if cpc else '—'}"
            + (f" · {esc(intent)}" if intent else "")
        )
    lines += ["", "<i>KD = keyword difficulty (0–100). 🟢 easy · 🟡 medium · 🔴 hard</i>"]
    return "\n".join(lines)
