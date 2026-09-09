"""Reliable web trade selector for Priyanithan.

The first Telegram button opens a small Render-hosted web selector instead of
relying on Telegram callback_query delivery. The selector gives the user 10
seconds to choose DEMO or REAL, then opens the official Olymptrade web
platform. The bot never places a broker order and AUTO_TRADE remains OFF.
"""
import html
import re
import sys
import threading
import time
from urllib.parse import quote

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

_INSTALLED = False
_BOT_SEND_PATCHED = False
_OLYMPTRADE_URL = "https://olymptrade.com/pages/trading/"
_OLYMPTRADE_DEMO_URL = "https://olymptrade.com/pages/trading/account/free-demo/"
_SIGNAL_RE = re.compile(
    r"🔥?\s*PRIYANITHAN AI SIGNAL\s*🔥?.*?"
    r"📈\s*([^\n]+).*?"
    r"(⬆️\s*UP|⬇️\s*DOWN).*?"
    r"💰\s*Entry:\s*([^\n]+).*?"
    r"⏱️\s*Expiry:\s*(\d+)\s*MIN",
    re.S | re.I,
)


def _get_app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    return sys.modules.get("app")


def _parse_signal(text):
    m = _SIGNAL_RE.search(str(text or ""))
    if not m:
        return None
    pair = m.group(1).strip()
    direction = "UP" if "UP" in m.group(2).upper() else "DOWN"
    entry = m.group(3).strip()
    try:
        expiry = int(m.group(4))
    except (TypeError, ValueError):
        return None
    if not pair or not entry or expiry not in (1, 2, 3, 5, 10, 15):
        return None
    return pair, direction, entry, expiry


def _selector_url(pair, direction, entry, expiry):
    appmod = _get_app()
    base = "https://priyanithan-ai.onrender.com"
    if appmod is not None:
        try:
            import os
            base = os.getenv("RENDER_EXTERNAL_URL", base).rstrip("/")
        except Exception:
            pass
    return (
        f"{base}/trade/select?pair={quote(pair, safe='')}&direction={quote(direction, safe='')}"
        f"&entry={quote(entry, safe='')}&expiry={int(expiry)}"
    )


def _button_for(text):
    """Open the reliable web selector; do not depend on Telegram callbacks."""
    data = _parse_signal(text)
    if data is None:
        return None
    pair, direction, entry, expiry = data
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⚡ SELECT DEMO / REAL (10s)", url=_selector_url(pair, direction, entry, expiry))]]
    )


def _install_selector_route(appmod):
    if getattr(appmod, "_PRIYANITHAN_WEB_SELECTOR", False):
        return
    flask_app = getattr(appmod, "app", None)
    if flask_app is None:
        return

    @flask_app.get("/trade/select")
    def trade_select_page():
        from flask import request

        pair = str(request.args.get("pair", "")).strip()
        direction = str(request.args.get("direction", "")).strip().upper()
        entry = str(request.args.get("entry", "")).strip()
        try:
            expiry = int(request.args.get("expiry", "0"))
        except ValueError:
            expiry = 0
        if not pair or direction not in ("UP", "DOWN") or not entry or expiry not in (1, 2, 3, 5, 10, 15):
            return "Invalid or expired trade signal", 400

        e_pair = html.escape(pair)
        e_dir = html.escape(direction)
        e_entry = html.escape(entry)
        demo_url = html.escape(_OLYMPTRADE_DEMO_URL, quote=True)
        real_url = html.escape(_OLYMPTRADE_URL, quote=True)
        arrow = "⬆️" if direction == "UP" else "⬇️"

        return f'''<!doctype html>
<html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Priyanithan Trade Selection</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#111827;color:#fff;margin:0;padding:24px}}
.card{{max-width:520px;margin:auto;background:#1f2937;border-radius:18px;padding:22px;box-shadow:0 10px 30px #0005}}
h1{{font-size:22px;margin:0 0 8px}} .muted{{color:#9ca3af}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:20px}}
a{{display:block;text-decoration:none;color:#fff;text-align:center;padding:16px 8px;border-radius:14px;font-weight:800}}
.demo{{background:#166534}} .real{{background:#991b1b}}
.warn{{margin-top:18px;padding:12px;border-radius:12px;background:#374151;font-size:13px}}
#timer{{font-size:28px;font-weight:900;text-align:center;margin:14px 0;color:#fbbf24}}
</style></head><body>
<div class="card">
<h1>🎯 TRADE MODE SELECTION</h1>
<div class="muted">Choose within <b>10 seconds</b>. This page opens the web platform only.</div>
<div id="timer">10</div>
<p>📈 <b>Asset:</b> {e_pair}</p>
<p>{arrow} <b>Direction:</b> {e_dir}</p>
<p>💰 <b>Entry reference:</b> {e_entry}</p>
<p>⏱️ <b>Expiry:</b> {expiry} MIN</p>
<div class="grid" id="choices">
<a class="demo" href="{demo_url}">🧪 DEMO — OPEN WEB</a>
<a class="real" href="{real_url}" onclick="return confirmReal(event)">🔴 REAL — OPEN WEB</a>
</div>
<div class="warn">⚠️ AUTO TRADE: OFF. The bot does not place the order. On Olymptrade, verify the exact asset, direction, amount and expiry before manual entry.</div>
</div>
<script>
let left=10;
const timer=document.getElementById('timer');
const choices=document.getElementById('choices');
const iv=setInterval(()=>{{left--;timer.textContent=left;if(left<=0){{clearInterval(iv);choices.style.opacity='.45';document.body.dataset.expired='1';timer.textContent='EXPIRED';}}}},1000);
function confirmReal(e){{
 if(document.body.dataset.expired==='1'){{e.preventDefault();return false;}}
 return confirm('REAL mode uses your own funds. Continue to the Olymptrade web platform?');
}}
</script></body></html>'''

    appmod._PRIYANITHAN_WEB_SELECTOR = True
    if hasattr(appmod, "log"):
        appmod.log.info("WEB TRADE SELECTOR ACTIVE: 10-second DEMO/REAL page enabled")


def _wrap_send(appmod):
    current = getattr(appmod, "send_to_recipients", None)
    if current is None or getattr(current, "_FAST_MANUAL_WRAPPER", False):
        return False

    async def patched_send(bot, text):
        markup = _button_for(text)
        if markup is None:
            return await current(bot, text)
        ids = appmod.recipients()
        if not ids:
            return await current(bot, text)
        try:
            bot_id = int((await bot.get_me()).id)
        except Exception:
            bot_id = None
        sent = False
        for chat_id in ids:
            if bot_id is not None and int(chat_id) == bot_id:
                continue
            try:
                await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
                sent = True
            except Exception as exc:
                appmod.log.warning("Fast manual signal send failed chat_id=%s: %s", chat_id, exc)
        return sent

    patched_send._FAST_MANUAL_WRAPPER = True
    patched_send._FAST_MANUAL_INNER = current
    appmod.send_to_recipients = patched_send
    appmod.log.info("FAST MANUAL ENTRY SEND WRAPPER ACTIVE: web selector enabled")
    return True


def _patch_bot_send_message(appmod):
    """Final fail-safe: attach the web selector to every approved signal."""
    global _BOT_SEND_PATCHED
    if _BOT_SEND_PATCHED:
        return False
    original = getattr(Bot, "send_message", None)
    if original is None or getattr(original, "_PRIYANITHAN_BUTTON_PATCH", False):
        _BOT_SEND_PATCHED = True
        return False

    async def patched_bot_send(self, *args, **kwargs):
        text = kwargs.get("text")
        if text is None and len(args) >= 2:
            text = args[1]
        markup = _button_for(text)
        if markup is not None and kwargs.get("reply_markup") is None:
            kwargs["reply_markup"] = markup
        return await original(self, *args, **kwargs)

    patched_bot_send._PRIYANITHAN_BUTTON_PATCH = True
    Bot.send_message = patched_bot_send
    _BOT_SEND_PATCHED = True
    appmod.log.info("TELEGRAM BOT-LAYER PATCH ACTIVE: approved signals use web DEMO/REAL selector")
    return True


def _install():
    global _INSTALLED
    appmod = _get_app()
    if appmod is None:
        return False
    _install_selector_route(appmod)
    _patch_bot_send_message(appmod)
    changed = _wrap_send(appmod)
    if changed:
        _INSTALLED = True
    return True


def bootstrap():
    for _ in range(1800):
        try:
            _install()
        except Exception:
            appmod = _get_app()
            if appmod is not None and hasattr(appmod, "log"):
                appmod.log.exception("WEB TRADE SELECTOR BOOTSTRAP FAILED")
        time.sleep(1.0)


threading.Thread(target=bootstrap, name="web-trade-selector-bootstrap", daemon=True).start()
