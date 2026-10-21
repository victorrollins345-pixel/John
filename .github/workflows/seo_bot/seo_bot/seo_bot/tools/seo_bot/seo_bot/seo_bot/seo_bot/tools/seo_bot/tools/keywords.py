"""Free keyword tools: keyword density and Google Autocomplete ideas / questions."""
from __future__ import annotations

import asyncio
import re
import string
from collections import Counter

import httpx

from .. import config
from ..utils import ProviderError, esc, fetch_ok, short
from .onpage import visible_text

STOPWORDS = set(
    """a about above after again against all also am an and any are aren't as at be because been before being
    below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during
    each few for from further get got had hadn't has hasn't have haven't having he he'd he'll he's her here here's
    hers herself him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself just let's
    may me more most mustn't my myself new no nor not now of off on once one only or other ought our ours ourselves
    out over own same shan't she she'd she'll she's should shouldn't so some such than that that's the their theirs
    them themselves then there there's these they they'd they'll they're they've this those through to too under
    until up us very via was wasn't we we'd we'll we're we've were weren't what what's when when's where where's
    which while who who's whom why why's will with won't would wouldn't you you'd you'll you're you've your yours
    yourself yourselves""".split()
)
WORD_RE = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)
QUESTION_WORDS = ["how", "what", "why", "where", "when", "who", "which", "can", "is", "are", "does", "should"]
MODIFIERS = ["best", "cheap", "free", "top", "near me", "vs", "for", "without", "with", "online", "price", "2026"]


def ngrams(tokens: list[str], n: int) -> Counter:
    counts: Counter = Counter()
    for i in range(len(tokens) - n + 1):
        gram = tokens[i : i + n]
        if gram[0] in STOPWORDS or gram[-1] in STOPWORDS:
            continue
        if n == 1 and len(gram[0]) < 3:
            continue
        counts[" ".join(gram)] += 1
    return counts


def density(text: str, top: int = 12) -> dict:
    tokens = [t.lower().replace("’", "'") for t in WORD_RE.findall(text)]
    total = len(tokens)
    return {
        "total": total,
        **{f"{n}": [(g, c, round(100 * c / max(total, 1), 2)) for g, c in ngrams(tokens, n).most_common(top)]
           for n in (1, 2, 3)},
    }


async def density_report(url: str) -> str:
    page = await fetch_ok(url)
    d = density(visible_text(page.text))
    lines = [f"<b>📊 Keyword density</b> — {esc(short(page.url, 80))}", f"Total words: {d['total']:,}", ""]
    for n, label in (("1", "Single words"), ("2", "2-word phrases"), ("3", "3-word phrases")):
        lines.append(f"<b>{label}</b>")
        if not d[n]:
            lines.append("—")
        for gram, count, pct in d[n]:
            lines.append(f"• {esc(gram)} — {count} ({pct}%)")
        lines.append("")
    lines.append("<i>Tip: a main keyword around 0.5–2% usually reads naturally. Avoid stuffing.</i>")
    return "\n".join(lines)


# --- Google Autocomplete -----------------------------------------------------------------
async def _complete(client: httpx.AsyncClient, sem: asyncio.Semaphore, query: str) -> list[str]:
    async with sem:
        try:
            resp = await client.get(
                "https://suggestqueries.google.com/complete/search",
                params={"client": "firefox", "q": query, "hl": config.SUGGEST_HL, "gl": config.SUGGEST_GL},
            )
            data = resp.json()
            return [s for s in data[1] if isinstance(s, str)] if len(data) > 1 else []
        except (httpx.HTTPError, ValueError, IndexError, TypeError):
            return []


async def autocomplete_many(queries: list[str]) -> list[list[str]]:
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "Mozilla/5.0"}) as client:
        return await asyncio.gather(*(_complete(client, sem, q) for q in queries))


def _unique(groups: list[list[str]], exclude: str) -> list[str]:
    seen, out = {exclude.lower()}, []
    for group in groups:
        for s in group:
            if s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
    return out


async def suggest_report(seed: str) -> str:
    seed = seed.strip().lower()
    queries = [seed] + [f"{seed} {c}" for c in string.ascii_lowercase]
    ideas = _unique(await autocomplete_many(queries), seed)
    if not ideas:
        raise ProviderError("Google returned no suggestions for that keyword. Try a broader term.")
    lines = [f"<b>💡 Keyword ideas</b> for “{esc(seed)}” ({len(ideas)} found)", ""]
    lines += [f"• {esc(s)}" for s in ideas[:80]]
    lines += ["", "<i>Source: Google Autocomplete. Add volumes &amp; difficulty with /ideas (DataForSEO).</i>"]
    return "\n".join(lines)


async def questions_report(seed: str) -> str:
    seed = seed.strip().lower()
    q_queries = [f"{w} {seed}" for w in QUESTION_WORDS]
    m_queries = [f"{seed} {m}" for m in MODIFIERS] + [f"{m} {seed}" for m in ("best", "top", "cheap")]
    q_groups, m_groups = await asyncio.gather(autocomplete_many(q_queries), autocomplete_many(m_queries))
    questions = _unique(q_groups, seed)
    modifiers = _unique(m_groups, seed)
    if not questions and not modifiers:
        raise ProviderError("Google returned no suggestions for that keyword.")
    lines = [f"<b>❓ Questions people ask</b> about “{esc(seed)}”", ""]
    lines += [f"• {esc(s)}" for s in questions[:40]] or ["—"]
    lines += ["", "<b>🔀 Modifier keywords</b>", ""]
    lines += [f"• {esc(s)}" for s in modifiers[:40]] or ["—"]
    lines += ["", "<i>Great for FAQ sections, blog topics and H2s.</i>"]
    return "\n".join(lines)
