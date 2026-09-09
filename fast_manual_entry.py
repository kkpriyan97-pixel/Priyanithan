"""Priyanithan final Trade Now flow.

START -> ACCESS -> DEMO/REAL -> AI scan -> 5-min signal -> Telegram Mini App
-> auto-prepared asset/time/price/direction -> Olymp Trade web -> manual final action.

The Mini App does not place orders or use broker credentials.
"""
import hashlib
import hmac
import html
import os
import re
import sys
import threading
import time
from urllib.parse import urlencode

os.environ.setdefault("RENDER_EXTERNAL_URL", "https://priyanithan.onrender.com")

from flask import request
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

MODES = {}
LOCK = threading.Lock()
BASE = "https://priyanithan.onrender.com"
DEMO_URL = "https://olymptrade.com/pages/trading/account/free-demo/"
REAL_URL = "https://olymptrade.com/pages/trading/"
EXPIRIES = (1, 2, 3, 5, 10, 15)
SIGNAL_RE = re.compile(
    r"🔥?\s*PRIYANITHAN AI SIGNAL\s*🔥?.*?"
    r"📈\s*([^\n]+).*?(⬆️\s*UP|⬇️\s*DOWN).*?"
    r"💰\s*Entry:\s*([^\n]+).*?⏱️\s*Expiry:\s*(\d+)\s*MIN",
    re.S | re.I,
)


def app():
    m = sys.modules.get("__main__")
    return m if m is not None and getattr(m, "__file__", "").endswith("app.py") else sys.modules.get("app")


def secret():
    return (os.getenv("ACCESS_CODE") or os.getenv("TELEGRAM_BOT_TOKEN") or "priyanithan-final").encode()


def sign(value):
    return hmac.new(secret(), value.encode(), hashlib.sha256).hexdigest()[:24]


def pack(value):
    return value + "." + sign(value)


def unpack(value):
    try:
        raw, sig = str(value).rsplit(".", 1)
    except ValueError:
        return None
    return raw if hmac.compare_digest(sig, sign(raw)) else None


def mode(cid):
    with LOCK:
        return MODES.get(int(cid))


def set_mode(cid, value):
    with LOCK:
        MODES[int(cid)] = value


def authorized_ids(a):
    try:
        return {int(x) for x in a.authorized_users}
    except Exception:
        return set()


def parse_signal(text):
    m = SIGNAL_RE.search(str(text or ""))
    if not m:
        return None
    try:
        expiry = int(m.group(4))
    except Exception:
        return None
    pair = m.group(1).strip()
    direction = "UP" if "UP" in m.group(2).upper() else "DOWN"
    entry = m.group(3).strip()
    return (pair, direction, entry, expiry) if pair and entry and expiry in EXPIRIES else None


def base_url():
    return os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/") or BASE


def mode_url(cid, value):
    raw = f"{int(cid)}|{value}|{int(time.time()) // 600}"
    return base_url() + "/trade/mode?" + urlencode({"token": pack(raw)})


def trade_app_url(cid, pair, direction, entry, expiry):
    # Five-minute bucket prevents stale signal links from being reused.
    raw = f"{int(cid)}|{pair}|{direction}|{entry}|{expiry}|{int(time.time()) // 300}"
    return base_url() + "/trade/app?" + urlencode({"token": pack(raw)})


def mode_markup(cid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🧪 DEMO", url=mode_url(cid, "DEMO")),
        InlineKeyboardButton("🔴 REAL", url=mode_url(cid, "REAL")),
    ]])


def signal_markup(cid, text):
    data = parse_signal(text)
    if not data or mode(cid) not in ("DEMO", "REAL"):
        return None
    pair, direction, entry, expiry = data
    # Telegram Mini App: opens inside Telegram and receives the exact signal values.
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "⚡ TRADE NOW",
            web_app=WebAppInfo(url=trade_app_url(cid, pair, direction, entry, expiry)),
        )
    ]])


def start_scan(cid):
    a = app()
    loop = getattr(a, "runtime_loop", None) if a else None
    application = getattr(a, "telegram_application", None) if a else None
    if not a or not loop or not application or mode(cid) not in ("DEMO", "REAL"):
        return False
    current = getattr(a, "manual_scan_task", None)
    if current is not None and not current.done():
        return True

    def kick():
        current = getattr(a, "manual_scan_task", None)
        if current is None or current.done():
            try:
                import asyncio
                a.manual_scan_task = asyncio.create_task(a.scan_cycle(application), name="manual-scan")
            except Exception as exc:
                a.log.warning("FINAL FLOW scan start failed: %s", exc)

    loop.call_soon_threadsafe(kick)
    return True


def patch_start(a):
    application = getattr(a, "telegram_application", None)
    if not application:
        return False
    for handlers in getattr(application, "handlers", {}).values():
        for handler in handlers:
            if "start" not in getattr(handler, "commands", set()):
                continue
            if getattr(handler.callback, "_FINAL_START", False):
                return True

            async def final_start(update, context):
                user = getattr(update, "effective_user", None)
                chat = getattr(update, "effective_chat", None)
                uid = getattr(user, "id", getattr(chat, "id", None))
                cid = getattr(chat, "id", None)
                if cid is None:
                    return
                try:
                    a.remember_chat(update)
                except Exception:
                    pass
                if uid not in authorized_ids(a):
                    await update.message.reply_text(
                        "🔐 ACCESS REQUIRED\n\nSend /access YOUR_ACCESS_CODE first, then /start again."
                    )
                    return
                if mode(uid) not in ("DEMO", "REAL"):
                    await update.message.reply_text(
                        "🎯 PRIYANITHAN AI TRADING\n\n"
                        "ACCESS: VERIFIED ✅\n"
                        "Choose DEMO or REAL before the scan.\n"
                        "🤖 AI confirmation is required.\n"
                        "⏱️ Signals run every 5 minutes.\n"
                        "⚠️ AUTO TRADE: OFF — manual trade only.",
                        reply_markup=mode_markup(uid),
                    )
                    return
                await update.message.reply_text(
                    f"✅ ACCESS OK | MODE: {mode(uid)}\n"
                    "🔎 Fresh scan started.\n"
                    "🤖 AI confirmation required.\n"
                    "⏱️ Next signal cycle: every 5 minutes."
                )
                start_scan(uid)

            final_start._FINAL_START = True
            handler.callback = final_start
            a.log.info("FINAL FLOW: /start -> access -> DEMO/REAL -> scan")
            return True
    return False


def install_routes(a):
    flask = getattr(a, "app", None)
    if flask is None:
        return
    rules = {r.rule for r in flask.url_map.iter_rules()}

    if "/trade/mode" not in rules:
        def trade_mode():
            raw = unpack(request.args.get("token", ""))
            if not raw:
                return "Invalid mode link", 403
            parts = raw.split("|")
            if len(parts) != 3 or parts[1] not in ("DEMO", "REAL"):
                return "Invalid mode link", 400
            try:
                cid, bucket = int(parts[0]), int(parts[2])
            except Exception:
                return "Invalid mode link", 400
            if abs(bucket - int(time.time()) // 600) > 1:
                return "Mode link expired. Send /start again.", 410
            if cid not in authorized_ids(a):
                return "Access not authorized. Send /access first.", 403
            value = parts[1]
            set_mode(cid, value)
            appx = getattr(a, "telegram_application", None)
            if appx is not None:
                async def notify():
                    await appx.bot.send_message(
                        chat_id=cid,
                        text=(f"✅ MODE SELECTED: {value}\n"
                              "🔎 Fresh scan started.\n"
                              "🤖 AI confirmation required.\n"
                              "⏱️ Signals every 5 minutes.\n"
                              "⚠️ AUTO TRADE: OFF — manual trade only.")
                    )
                try:
                    import asyncio
                    asyncio.run_coroutine_threadsafe(notify(), a.runtime_loop)
                except Exception:
                    pass
            start_scan(cid)
            return (
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<body style='font-family:system-ui;padding:28px'><h2>"
                f"{'🧪' if value == 'DEMO' else '🔴'} {value} MODE SELECTED</h2>"
                "<p>Fresh scan started.</p><p>AI confirmation → qualified signal → TRADE NOW.</p>"
                "<b>AUTO TRADE: OFF</b></body>"
            )

        flask.add_url_rule("/trade/mode", endpoint="final_trade_mode", view_func=trade_mode)

    if "/trade/app" not in {r.rule for r in flask.url_map.iter_rules()}:
        def trade_app():
            raw = unpack(request.args.get("token", ""))
            if not raw:
                return "Invalid trade link", 403
            parts = raw.split("|")
            if len(parts) != 6:
                return "Invalid trade link", 400
            cid, pair, direction, entry, expiry_s, bucket_s = parts
            try:
                cid, expiry, bucket = int(cid), int(expiry_s), int(bucket_s)
            except Exception:
                return "Invalid trade link", 400
            if cid not in authorized_ids(a):
                return "Access not authorized.", 403
            if direction not in ("UP", "DOWN") or expiry not in EXPIRIES:
                return "Invalid trade signal", 400
            if abs(bucket - int(time.time()) // 300) > 1:
                return "Trade signal expired. Wait for the next signal.", 410
            selected_mode = mode(cid)
            if selected_mode not in ("DEMO", "REAL"):
                return "Select DEMO or REAL from /start first.", 403
            olymp_url = DEMO_URL if selected_mode == "DEMO" else REAL_URL
            direction_label = "⬆️ UP / BUY" if direction == "UP" else "⬇️ DOWN / SELL"
            # This page is the automation layer we can safely control from Telegram:
            # exact signal fields are populated automatically. The external broker UI is not
            # programmatically clicked and the final trade remains manual.
            return f"""
<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Priyanithan Trade Now</title>
<style>
body{{margin:0;background:#0b1220;color:#fff;font-family:-apple-system,BlinkMacSystemFont,system-ui;padding:18px}}
.card{{max-width:520px;margin:auto;background:#172033;border-radius:22px;padding:22px;box-shadow:0 8px 30px #0005}}
h2{{margin-top:0}} .row{{display:flex;justify-content:space-between;padding:14px 0;border-bottom:1px solid #ffffff18}}
.label{{color:#9ca3af}} .value{{font-weight:800}} .auto{{color:#86efac;font-size:12px;margin-left:6px}}
.open{{display:block;text-align:center;text-decoration:none;background:#16a34a;color:#fff;padding:17px;border-radius:14px;font-weight:900;margin-top:22px}}
.note{{font-size:13px;color:#cbd5e1;line-height:1.5;margin-top:18px}}
</style></head><body><div class="card">
<h2>⚡ TRADE NOW</h2>
<div class="row"><span class="label">MODE</span><span class="value">{html.escape(selected_mode)} <span class="auto">AUTO</span></span></div>
<div class="row"><span class="label">ASSET</span><span class="value">{html.escape(pair)} <span class="auto">AUTO</span></span></div>
<div class="row"><span class="label">DIRECTION</span><span class="value">{direction_label} <span class="auto">AUTO</span></span></div>
<div class="row"><span class="label">ENTRY / PRICE REF</span><span class="value">{html.escape(entry)} <span class="auto">AUTO</span></span></div>
<div class="row"><span class="label">EXPIRY / TIME</span><span class="value">{expiry} MIN <span class="auto">AUTO</span></span></div>
<a class="open" href="{html.escape(olymp_url, quote=True)}">OPEN OLYMP TRADE</a>
<div class="note">✅ Asset, time, price reference and direction are automatically carried from the AI-confirmed signal into this Trade Now screen.<br><br>⚠️ AUTO TRADE: OFF. The broker page is opened for you, but the final broker selection/order action must be verified and completed manually.</div>
</div></body></html>"""

        flask.add_url_rule("/trade/app", endpoint="final_trade_app", view_func=trade_app)


def patch_senders(a):
    original = getattr(Bot, "send_message", None)
    if original and not getattr(original, "_FINAL_BOT_SEND", False):
        async def final_send(self, *args, **kwargs):
            text = kwargs.get("text", args[1] if len(args) >= 2 else "")
            data = parse_signal(text)
            if data:
                cid = kwargs.get("chat_id", args[0] if args else None)
                if mode(cid) not in ("DEMO", "REAL"):
                    return None
                if kwargs.get("reply_markup") is None:
                    kwargs["reply_markup"] = signal_markup(cid, text)
            return await original(self, *args, **kwargs)
        final_send._FINAL_BOT_SEND = True
        Bot.send_message = final_send

    current = getattr(a, "send_to_recipients", None)
    if current and not getattr(current, "_FINAL_RECIPIENTS", False):
        async def final_recipients(bot, text):
            data = parse_signal(text)
            if not data:
                return await current(bot, text)
            sent = False
            for cid in a.recipients():
                if mode(cid) not in ("DEMO", "REAL"):
                    continue
                try:
                    await bot.send_message(
                        chat_id=cid,
                        text=text,
                        reply_markup=signal_markup(cid, text),
                    )
                    sent = True
                except Exception as exc:
                    a.log.warning("FINAL signal send failed %s: %s", cid, exc)
            return sent
        final_recipients._FINAL_RECIPIENTS = True
        a.send_to_recipients = final_recipients


def install():
    a = app()
    if not a:
        return False
    install_routes(a)
    patch_senders(a)
    patch_start(a)
    return True


try:
    install()
except Exception:
    pass


def bootstrap():
    for _ in range(1800):
        try:
            install()
        except Exception:
            a = app()
            if a and hasattr(a, "log"):
                a.log.exception("FINAL FLOW bootstrap failed")
        time.sleep(1)


threading.Thread(target=bootstrap, name="priyanithan-final-flow", daemon=True).start()
