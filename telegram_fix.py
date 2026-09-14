from __future__ import annotations

import json
import logging
import os
import threading
import time

import requests

import app

log = logging.getLogger("candice.telegram_fix")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CONFIGURED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ACTIVE_CHAT_ID = CONFIGURED_CHAT_ID
OFFSET = 0
BIND_LOCK = threading.RLock()


def _api(method: str, payload=None, timeout=15):
    if not TOKEN:
        return False, None, "TELEGRAM_BOT_TOKEN missing"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/{method}",
            json=payload or {},
            timeout=timeout,
        )
        try:
            data = r.json()
        except Exception:
            data = {}
        if not r.ok or not data.get("ok", False):
            code = data.get("error_code", r.status_code)
            desc = data.get("description", "unknown Telegram API error")
            log.warning("Telegram API failed method=%s code=%s description=%s", method, code, desc)
            return False, data, str(desc)
        return True, data, ""
    except Exception as exc:
        log.warning("Telegram API request failed method=%s error=%s", method, exc.__class__.__name__)
        return False, None, exc.__class__.__name__


def telegram(text, chat_id=None):
    target = str(chat_id or ACTIVE_CHAT_ID or CONFIGURED_CHAT_ID).strip()
    if not target:
        log.warning("Telegram send skipped: no active chat id yet")
        return False
    ok, _, _ = _api("sendMessage", {
        "chat_id": target,
        "text": text,
        "disable_web_page_preview": True,
    })
    return ok


def _get_me():
    ok, data, _ = _api("getMe", {}, timeout=10)
    if ok:
        bot = (data or {}).get("result", {})
        log.info("Telegram bot verified: id=%s username=@%s", bot.get("id"), bot.get("username"))
    return ok


def telegram_command_loop():
    global OFFSET, ACTIVE_CHAT_ID
    if not TOKEN:
        log.warning("Telegram command listener disabled: token missing")
        return
    _get_me()
    log.info("Telegram command listener started (chat auto-bind enabled)")
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                params={
                    "timeout": 25,
                    "offset": OFFSET + 1,
                    "allowed_updates": json.dumps(["message"]),
                },
                timeout=35,
            )
            try:
                data = r.json()
            except Exception:
                data = {}
            if r.status_code == 401 or not data.get("ok", r.ok):
                code = data.get("error_code", r.status_code)
                desc = data.get("description", "unknown Telegram API error")
                log.error("Telegram polling failed code=%s description=%s", code, desc)
                time.sleep(30)
                continue
            for u in data.get("result", []):
                OFFSET = max(OFFSET, int(u.get("update_id", OFFSET)))
                m = u.get("message") or {}
                chat_obj = m.get("chat") or {}
                chat = str(chat_obj.get("id", "")).strip()
                if not chat:
                    continue
                text = str(m.get("text", "")).strip().lower()

                # A private /start proves that this is a chat the bot can receive from.
                # Bind that chat in memory so an old/wrong TELEGRAM_CHAT_ID cannot block delivery.
                if text.startswith("/start"):
                    with BIND_LOCK:
                        ACTIVE_CHAT_ID = chat
                    log.info("Telegram chat bound from /start: chat_type=%s", chat_obj.get("type", "unknown"))
                    telegram(
                        "🎯 CANDICE AI 12.0\n\n"
                        "✅ ONLINE\n"
                        "📡 Olymp Trade LIVE READ-ONLY\n"
                        "🕐 1m analysis + 3m/5m confirmation\n"
                        "🧠 Adaptive technical brain\n"
                        "⏱️ 2 / 3 / 5 / 10 / 15 min\n"
                        "🛡️ DEMO / MANUAL ONLY\n"
                        "🚫 AUTO-TRADE OFF • MARTINGALE OFF\n\n"
                        "/status • connection\n/performance • session",
                        chat,
                    )
                    continue

                # Only accept commands from the configured or previously bound chat.
                if chat != str(ACTIVE_CHAT_ID or CONFIGURED_CHAT_ID).strip():
                    continue

                if text.startswith("/status"):
                    s = app.live_feed.status() if app.live_feed else {"connected": False, "assets": []}
                    telegram(
                        f"🎯 STATUS\n\n📡 Olymp • {'🟢 CONNECTED' if s.get('connected') else '🔴 NOT CONNECTED'}\n"
                        f"📈 Assets • {', '.join(s.get('assets', [])) or '-'}\n"
                        "🕐 1m live + 3m/5m derived\n🧠 Adaptive brain • ON\n"
                        "🛡️ Read-only • YES\n🚫 Auto-trade • OFF",
                        chat,
                    )
                elif text.startswith("/performance"):
                    app.reset_risk()
                    telegram(
                        f"📊 SESSION\nSignals • {app.risk['signals']}\nWins • {app.risk['wins']}\n"
                        f"Losses • {app.risk['losses_total']}\nStreak • {app.risk['streak']}\n"
                        f"Daily stop • {app.MAX_DAILY_LOSSES}\nConsecutive stop • {app.MAX_CONSECUTIVE_LOSSES}",
                        chat,
                    )
        except Exception as exc:
            log.warning("Telegram polling error: %s", exc.__class__.__name__)
            time.sleep(5)


# Replace only Telegram transport/polling. Trading logic and Olymp read-only feed stay untouched.
app.telegram = telegram
app.telegram_command_loop = telegram_command_loop

if __name__ == "__main__":
    app.main()
