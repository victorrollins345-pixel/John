"""@MiniSEOToolsBot — a SEMrush-style SEO toolkit inside Telegram."""
from __future__ import annotations

import asyncio
import datetime as dt
import functools
import logging
import re
import time

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, Defaults, MessageHandler,
                          filters)

from . import config
from .tools import dataforseo, keywords, onpage, serp, technical, tracking
from .utils import BotError, domain_of, esc, split_message

log = logging.getLogger("seo_bot")
_last_call: dict[int, float] = {}

# ---------------------------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------------------------
SECTIONS = {
    "audit": ("🩺 Site Audit & On-Page", [
        ("audit", "&lt;url&gt;", "Full on-page SEO audit with a score"),
        ("meta", "&lt;url&gt;", "Title, description, canonical, OG &amp; schema tags"),
        ("headings", "&lt;url&gt;", "H1–H6 outline"),
        ("density", "&lt;url&gt;", "Keyword density (1-3 word phrases)"),
        ("compare", "&lt;url1&gt; &lt;url2&gt;", "Compare two pages side by side"),
        ("brokenlinks", "&lt;url&gt;", "Find broken links on a page"),
    ]),
    "tech": ("⚙️ Technical SEO", [
        ("speed", "&lt;url&gt; [mobile|desktop]", "PageSpeed &amp; Core Web Vitals"),
        ("robots", "&lt;domain&gt;", "robots.txt analysis"),
        ("sitemap", "&lt;domain&gt;", "XML sitemap check"),
        ("redirects", "&lt;domain&gt;", "http/https/www redirect check"),
        ("security", "&lt;url&gt;", "Security headers"),
        ("ssl", "&lt;domain&gt;", "SSL certificate"),
        ("dns", "&lt;domain&gt;", "DNS, SPF, DMARC"),
        ("whois", "&lt;domain&gt;", "WHOIS &amp; domain age"),
    ]),
    "kw": ("🔑 Keyword Research", [
        ("suggest", "&lt;keyword&gt;", "Keyword ideas (Google Autocomplete) — free"),
        ("questions", "&lt;keyword&gt;", "Questions &amp; modifiers people search — free"),
        ("ideas", "&lt;keyword&gt;", "Ideas with volume, KD &amp; CPC ⭐"),
        ("volume", "&lt;kw1, kw2, …&gt;", "Search volume, CPC, competition ⭐"),
    ]),
    "domain": ("📈 Domain & Competitor Analytics", [
        ("overview", "&lt;domain&gt;", "Traffic, keywords, positions ⭐"),
        ("rankedkw", "&lt;domain&gt;", "Top organic keywords ⭐"),
        ("competitors", "&lt;domain&gt;", "Organic competitors ⭐"),
        ("backlinks", "&lt;domain&gt;", "Backlinks, referring domains, anchors ⭐"),
    ]),
    "rank": ("🎯 Rank Tracking", [
        ("serp", "&lt;keyword&gt;", "Google top 20 for a keyword ⭐"),
        ("rank", "&lt;domain&gt; &lt;keyword&gt;", "Current position of a domain ⭐"),
        ("track", "&lt;domain&gt; &lt;keyword&gt;", "Track daily, get alerts on changes ⭐"),
        ("tracklist", "", "Your tracked keywords"),
        ("trackcheck", "", "Re-check all tracked keywords now ⭐"),
        ("untrack", "&lt;id&gt;", "Stop tracking"),
    ]),
}


def section_text(key: str) -> str:
    title, cmds = SECTIONS[key]
    lines = [f"<b>{title}</b>", ""]
    for name, args, desc in cmds:
        lines.append(f"/{name} {args}\n   {desc}")
    lines += ["", "⭐ = uses a paid data source (DataForSEO / Serper)"]
    return "\n".join(lines)


def menu_keyboard() -> InlineKeyboardMarkup:
    keys = list(SECTIONS)
    rows = [[InlineKeyboardButton(SECTIONS[k][0], callback_data=f"help:{k}")] for k in keys]
    return InlineKeyboardMarkup(rows)


WELCOME = (
    "👋 <b>Welcome to Mini SEO Tools!</b>\n\n"
    "Your pocket SEO toolkit — audits, keyword research, rank tracking, backlinks and competitor data.\n\n"
    "<b>Quick start:</b> just send me any URL to audit it, or try:\n"
    "• <code>/audit example.com</code>\n"
    "• <code>/suggest running shoes</code>\n"
    "• <code>/speed example.com</code>\n\n"
    "Pick a category to see all tools 👇"
)


# ---------------------------------------------------------------------------------------------
# Command decorator: access control, cooldown, typing indicator, friendly errors
# ---------------------------------------------------------------------------------------------
async def reply(update: Update, text: str, **kwargs) -> None:
    msg = update.effective_message
    for chunk in split_message(text):
        await msg.reply_text(chunk, **kwargs)


def command(min_args: int = 0, usage: str = "", slow: bool = False):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = update.effective_user
            if not user or not update.effective_message:
                return
            if config.ALLOWED_USER_IDS and user.id not in config.ALLOWED_USER_IDS:
                await reply(update, "⛔ This bot is private.")
                return
            args = context.args or []
            if len(args) < min_args:
                await reply(update, f"Usage: <code>{usage}</code>")
                return
            now = time.monotonic()
            wait = config.COOLDOWN_SECONDS - (now - _last_call.get(user.id, 0))
            if wait > 0 and user.id not in config.ADMIN_USER_IDS:
                await reply(update, f"⏳ Please wait {wait:.0f}s between commands.")
                return
            _last_call[user.id] = now
            try:
                tracking.log_usage(user.id, fn.__name__.removeprefix("cmd_"))
            except Exception:  # never block a command on stats
                log.warning("usage logging failed", exc_info=True)

            status_msg = None
            if slow:
                status_msg = await update.effective_message.reply_text("⏳ Working on it… this can take up to a minute.")
            typing = asyncio.create_task(_keep_typing(context, update.effective_chat.id))
            try:
                await fn(update, context)
            except BotError as exc:
                await reply(update, f"⚠️ {esc(exc)}")
            except Exception:
                log.exception("Command %s failed", fn.__name__)
                await reply(update, "❌ Something went wrong. Please try again in a moment.")
            finally:
                typing.cancel()
                if status_msg:
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
        return wrapper
    return decorator


async def _keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    try:
        while True:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4.5)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


def joined(context: ContextTypes.DEFAULT_TYPE, start: int = 0) -> str:
    return " ".join(context.args[start:]).strip()


# ---------------------------------------------------------------------------------------------
# Basic commands
# ---------------------------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(WELCOME, reply_markup=menu_keyboard())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "\n\n".join(section_text(k).rsplit("\n\n", 1)[0] for k in SECTIONS)
    text += "\n\n⭐ = uses a paid data source (DataForSEO / Serper)\n💡 Tip: send any URL to audit it instantly."
    await reply(update, text)


async def on_help_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    key = query.data.split(":", 1)[1]
    if key == "menu":
        await query.edit_message_text(WELCOME, reply_markup=menu_keyboard())
        return
    back = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="help:menu")]])
    await query.edit_message_text(section_text(key), reply_markup=back)


# --- On-page --------------------------------------------------------------------------------------
@command(1, "/audit example.com", slow=True)
async def cmd_audit(update, context):
    await reply(update, await onpage.audit_report(context.args[0]))


@command(1, "/meta example.com")
async def cmd_meta(update, context):
    await reply(update, await onpage.meta_report(context.args[0]))


@command(1, "/headings example.com")
async def cmd_headings(update, context):
    await reply(update, await onpage.headings_report(context.args[0]))


@command(1, "/density example.com/blog/post")
async def cmd_density(update, context):
    await reply(update, await keywords.density_report(context.args[0]))


@command(2, "/compare site-a.com site-b.com", slow=True)
async def cmd_compare(update, context):
    await reply(update, await onpage.compare_report(context.args[0], context.args[1]))


@command(1, "/brokenlinks example.com", slow=True)
async def cmd_brokenlinks(update, context):
    await reply(update, await technical.broken_links_report(context.args[0]))


# --- Technical ----------------------------------------------------------------------------------------
@command(1, "/speed example.com [mobile|desktop]", slow=True)
async def cmd_speed(update, context):
    strategy = "desktop" if len(context.args) > 1 and context.args[1].lower().startswith("d") else "mobile"
    await reply(update, await technical.pagespeed_report(context.args[0], strategy))


@command(1, "/robots example.com")
async def cmd_robots(update, context):
    await reply(update, await technical.robots_report(context.args[0]))


@command(1, "/sitemap example.com", slow=True)
async def cmd_sitemap(update, context):
    await reply(update, await technical.sitemap_report(context.args[0]))


@command(1, "/redirects example.com")
async def cmd_redirects(update, context):
    await reply(update, await technical.redirects_report(context.args[0]))


@command(1, "/security example.com")
async def cmd_security(update, context):
    await reply(update, await technical.security_report(context.args[0]))


@command(1, "/ssl example.com")
async def cmd_ssl(update, context):
    await reply(update, await technical.ssl_report(context.args[0]))


@command(1, "/dns example.com")
async def cmd_dns(update, context):
    await reply(update, await technical.dns_report(context.args[0]))


@command(1, "/whois example.com")
async def cmd_whois(update, context):
    await reply(update, await technical.whois_report(context.args[0]))


# --- Keywords ------------------------------------------------------------------------------------------
@command(1, "/suggest seo tools")
async def cmd_suggest(update, context):
    await reply(update, await keywords.suggest_report(joined(context)))


@command(1, "/questions seo tools")
async def cmd_questions(update, context):
    await reply(update, await keywords.questions_report(joined(context)))


@command(1, "/ideas seo tools", slow=True)
async def cmd_ideas(update, context):
    await reply(update, await dataforseo.ideas_report(joined(context)))


@command(1, "/volume seo tools, keyword research, backlink checker")
async def cmd_volume(update, context):
    text = joined(context)
    kws = [k for k in re.split(r"[,\n;]+", text) if k.strip()]
    await reply(update, await dataforseo.volume_report(kws))


# --- Domain analytics ---------------------------------------------------------------------------------
@command(1, "/overview example.com", slow=True)
async def cmd_overview(update, context):
    await reply(update, await dataforseo.overview_report(context.args[0]))


@command(1, "/rankedkw example.com", slow=True)
async def cmd_rankedkw(update, context):
    await reply(update, await dataforseo.ranked_keywords_report(context.args[0]))


@command(1, "/competitors example.com", slow=True)
async def cmd_competitors(update, context):
    await reply(update, await dataforseo.competitors_report(context.args[0]))


@command(1, "/backlinks example.com", slow=True)
async def cmd_backlinks(update, context):
    await reply(update, await dataforseo.backlinks_report(context.args[0]))


# --- SERP & rank tracking -----------------------------------------------------------------------------
@command(1, "/serp best seo tools", slow=True)
async def cmd_serp(update, context):
    await reply(update, await serp.serp_report(joined(context)))


@command(2, "/rank example.com best seo tools", slow=True)
async def cmd_rank(update, context):
    await reply(update, await serp.rank_report(context.args[0], joined(context, 1)))


@command(2, "/track example.com best seo tools", slow=True)
async def cmd_track(update, context):
    domain, keyword = domain_of(context.args[0]), joined(context, 1).lower()
    if not config.serp_enabled():
        await reply(update, f"⚠️ {esc(serp.NEEDS_SERP)}")
        return
    try:
        tid = tracking.add(update.effective_chat.id, domain, keyword)
    except tracking.TrackingError as exc:
        await reply(update, f"⚠️ {esc(exc)}")
        return
    hit = await serp.find_rank(domain, keyword)
    tracking.record(tid, hit["position"] if hit else None, hit["url"] if hit else None)
    pos = f"#{hit['position']}" if hit else "not in top 100"
    await reply(update, f"✅ Now tracking <b>{esc(domain)}</b> for “{esc(keyword)}” (id {tid}).\n"
                        f"Current position: <b>{pos}</b>\nI'll check daily and message you when it changes.")


@command(0)
async def cmd_tracklist(update, context):
    rows = tracking.list_for_chat(update.effective_chat.id)
    if not rows:
        await reply(update, "You're not tracking anything yet. Try <code>/track example.com best seo tools</code>")
        return
    lines = ["<b>🎯 Tracked keywords</b>", ""]
    for r in rows:
        lines.append(f"<code>{r['id']}</code> {esc(r['domain'])} — “{esc(r['keyword'])}”: "
                     f"{tracking.change_text(r['last_pos'], r['prev_pos'], r['checks'])}")
    lines += ["", "<i>Remove with /untrack &lt;id&gt;</i>"]
    await reply(update, "\n".join(lines))


@command(1, "/untrack 3")
async def cmd_untrack(update, context):
    try:
        tid = int(context.args[0])
    except ValueError:
        await reply(update, "Usage: <code>/untrack 3</code> — see ids with /tracklist")
        return
    ok = tracking.remove(update.effective_chat.id, tid)
    await reply(update, "🗑 Removed." if ok else "No tracked keyword with that id. See /tracklist")


@command(0, slow=True)
async def cmd_trackcheck(update, context):
    rows = tracking.list_for_chat(update.effective_chat.id)
    if not rows:
        await reply(update, "Nothing tracked yet. Use /track first.")
        return
    await reply(update, await check_rows(rows, header="🎯 <b>Rank check</b>"))


async def check_rows(rows, header: str, only_changes: bool = False) -> str:
    sem = asyncio.Semaphore(3)

    async def one(row):
        async with sem:
            try:
                hit = await serp.find_rank(row["domain"], row["keyword"])
            except BotError as exc:
                return row, None, str(exc)
            pos = hit["position"] if hit else None
            tracking.record(row["id"], pos, hit["url"] if hit else None)
            return row, pos, ""

    results = await asyncio.gather(*(one(r) for r in rows))
    lines, changed = [header, ""], 0
    for row, pos, err in results:
        if err:
            lines.append(f"⚠️ {esc(row['domain'])} — “{esc(row['keyword'])}”: {esc(err)}")
            continue
        prev = row["last_pos"]
        checks = row["checks"] + 1
        if pos != prev:
            changed += 1
        elif only_changes:
            continue
        lines.append(f"{esc(row['domain'])} — “{esc(row['keyword'])}”: {tracking.change_text(pos, prev, checks)}")
    if only_changes and changed == 0:
        return ""
    return "\n".join(lines)


async def daily_tracking_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = tracking.all_tracked()
    by_chat: dict[int, list] = {}
    for r in rows:
        by_chat.setdefault(r["chat_id"], []).append(r)
    for chat_id, chat_rows in by_chat.items():
        try:
            text = await check_rows(chat_rows, header="📬 <b>Daily rank update</b> (changes only)", only_changes=True)
            for chunk in split_message(text) if text else []:
                await context.bot.send_message(chat_id, chunk)
        except Exception:
            log.exception("Daily tracking failed for chat %s", chat_id)


# --- Admin & fallback --------------------------------------------------------------------------------
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or user.id not in config.ADMIN_USER_IDS:
        return
    s = tracking.stats()
    top = "\n".join(f"• /{r['command']}: {r['c']}" for r in s["top"]) or "—"
    await reply(update, (
        f"<b>📊 Bot stats</b>\n\nUsers: {s['users']} (7d active: {s['users_7d']})\n"
        f"Commands: {s['commands']} (24h: {s['commands_24h']})\nTracked keywords: {s['tracked']}\n\n"
        f"<b>Top commands</b>\n{top}\n\n"
        f"DataForSEO: {'✅' if config.dataforseo_enabled() else '❌'} · "
        f"Serper: {'✅' if config.SERPER_API_KEY else '❌'} · "
        f"PageSpeed key: {'✅' if config.PAGESPEED_API_KEY else '—'}"
    ))


URL_RE = re.compile(r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}(/\S*)?$", re.I)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    if URL_RE.match(text):
        context.args = [text]
        await cmd_audit(update, context)
    else:
        await update.effective_message.reply_text(
            "Send me a URL to audit it, or use /help to see all tools.", reply_markup=menu_keyboard())


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled error", exc_info=context.error)


# ---------------------------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------------------------
COMMANDS = {
    "start": cmd_start, "help": cmd_help,
    "audit": cmd_audit, "meta": cmd_meta, "headings": cmd_headings, "density": cmd_density,
    "compare": cmd_compare, "brokenlinks": cmd_brokenlinks,
    "speed": cmd_speed, "robots": cmd_robots, "sitemap": cmd_sitemap, "redirects": cmd_redirects,
    "security": cmd_security, "ssl": cmd_ssl, "dns": cmd_dns, "whois": cmd_whois,
    "suggest": cmd_suggest, "questions": cmd_questions, "ideas": cmd_ideas, "volume": cmd_volume,
    "overview": cmd_overview, "rankedkw": cmd_rankedkw, "competitors": cmd_competitors, "backlinks": cmd_backlinks,
    "serp": cmd_serp, "rank": cmd_rank, "track": cmd_track, "tracklist": cmd_tracklist,
    "untrack": cmd_untrack, "trackcheck": cmd_trackcheck, "stats": cmd_stats,
}


async def post_init(app: Application) -> None:
    menu = [BotCommand("start", "Main menu"), BotCommand("help", "All tools")]
    for _, cmds in SECTIONS.values():
        for name, _, desc in cmds:
            plain = re.sub(r"&\w+;", "", desc).replace("⭐", "").strip()
            menu.append(BotCommand(name, plain[:256]))
    await app.bot.set_my_commands(menu)
    me = await app.bot.get_me()
    log.info("Bot @%s is running. DataForSEO=%s Serper=%s", me.username,
             config.dataforseo_enabled(), bool(config.SERPER_API_KEY))


def build_app() -> Application:
    defaults = Defaults(parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True))
    app = (Application.builder().token(config.BOT_TOKEN).defaults(defaults)
           .concurrent_updates(True).post_init(post_init).build())
    for name, handler in COMMANDS.items():
        app.add_handler(CommandHandler(name, handler))
    app.add_handler(CallbackQueryHandler(on_help_button, pattern=r"^help:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    app.job_queue.run_daily(daily_tracking_job, time=dt.time(hour=config.TRACK_HOUR_UTC, tzinfo=dt.timezone.utc),
                            name="daily-rank-tracking")
    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not config.BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set. Add it in Railway → Variables.")
    tracking.init_db()
    build_app().run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
