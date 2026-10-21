"""Offline tests (no network). Run: pytest -q"""
import asyncio
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

import httpx
import pytest

from seo_bot.tools import dataforseo, keywords, onpage, serp, tracking
from seo_bot.utils import FetchError, NotConfigured, Page, domain_of, host_matches, normalize_url, split_message

HTML = """<!doctype html><html lang="en"><head><title>Best Running Shoes 2026 – Reviews & Buying Guide</title>
<meta name="description" content="We tested 40 running shoes to find the best for beginners, marathons and trails. Honest reviews and a buying guide.">
<meta name="viewport" content="width=device-width"><link rel="canonical" href="https://shoes.example/best/">
<meta property="og:title" content="Best Running Shoes"><meta property="og:description" content="x">
<link rel="icon" href="/f.ico"><script type="application/ld+json">{"@graph":[{"@type":"Article"},{"@type":"FAQPage"}]}</script>
<script>var hidden = "should not count";</script></head><body>
<h1>Best Running Shoes</h1><h2>Top picks</h2><h2>Buying guide</h2>
<p>""" + ("Running shoes for running fast. Best running shoes help beginners run marathons. " * 40) + """</p>
<img src=a.jpg alt="shoe"><img src=b.jpg>
<a href="/a">a</a><a href="/b">b</a><a href="https://www.shoes.example/c">c</a>
<a href="https://other.com" rel="nofollow">o</a><a href="mailto:x@y">m</a></body></html>"""


def make_page() -> Page:
    return Page(url="https://shoes.example/best/", status=200, headers=httpx.Headers({"content-encoding": "br"}),
                text=HTML, size=len(HTML), elapsed=0.4, redirects=[(301, "http://shoes.example/best")])


def test_audit():
    a = onpage.audit_page(make_page())
    d = a.data
    assert (d["internal_links"], d["external_links"], d["nofollow_links"]) == (3, 1, 1)
    assert d["schema"] == ["Article", "FAQPage"]
    assert d["images_no_alt"] == 1
    assert "hidden" not in onpage.visible_text(HTML)
    assert a.score > 80
    assert "Score" in onpage.format_audit(a)


def test_density():
    d = keywords.density(onpage.visible_text(HTML))
    assert d["2"][0][0] == "running shoes"


def test_helpers():
    assert normalize_url("example.com") == "https://example.com"
    assert domain_of("http://www.Example.com/x") == "example.com"
    assert host_matches("blog.example.com", "example.com")
    assert not host_matches("notexample.com", "example.com")
    with pytest.raises(FetchError):
        normalize_url("hello")
    long = "\n".join(f"<b>{i}</b> " + "x" * 80 for i in range(200))
    assert all(len(p) <= 3900 for p in split_message(long))


def test_tracking():
    tracking.init_db()
    tid = tracking.add(1, "example.com", "seo tools")
    tracking.record(tid, 12, "u")
    tracking.record(tid, 8, "u")
    row = tracking.list_for_chat(1)[0]
    assert (row["last_pos"], row["prev_pos"]) == (8, 12)
    assert "+4" in tracking.change_text(row["last_pos"], row["prev_pos"], row["checks"])
    with pytest.raises(tracking.TrackingError):
        tracking.add(1, "example.com", "seo tools")
    assert tracking.remove(1, tid)


def test_paid_features_explain_setup(monkeypatch):
    from seo_bot import config
    monkeypatch.setattr(config, "DFS_LOGIN", "")
    monkeypatch.setattr(config, "SERPER_API_KEY", "")
    for coro in (dataforseo.overview_report("example.com"), serp.get_serp("x")):
        with pytest.raises(NotConfigured):
            asyncio.run(coro)


def test_bot_builds():
    os.environ["TELEGRAM_BOT_TOKEN"] = "123456:TEST"
    from seo_bot import bot, config
    config.BOT_TOKEN = "123456:TEST"
    app = bot.build_app()
    assert len(app.handlers[0]) >= len(bot.COMMANDS)
