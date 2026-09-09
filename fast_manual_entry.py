"""Final Priyanithan workflow: START -> ACCESS -> DEMO/REAL -> AI scan -> 5-min signals -> TRADE NOW -> manual web trade."""
import hashlib, hmac, html, os, re, sys, threading, time
from urllib.parse import urlencode
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

MODES = {}
LOCK = threading.Lock()
PATCHED = False
START_PATCHED = False
BOT_PATCHED = False
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

def sign(v):
    return hmac.new(secret(), v.encode(), hashlib.sha256).hexdigest()[:24]

def pack(v):
    return v + "." + sign(v)

def unpack(v):
    try: raw, sig = str(v).rsplit(".", 1)
    except ValueError: return None
    return raw if hmac.compare_digest(sig, sign(raw)) else None

def mode(cid):
    with LOCK: return MODES.get(int(cid))

def set_mode(cid, value):
    with LOCK: MODES[int(cid)] = value

def authorized_ids(a):
    try: return {int(x) for x in a.authorized_users}
    except Exception: return set()

def parse_signal(text):
    m = SIGNAL_RE.search(str(text or ""))
    if not m: return None
    try: expiry = int(m.group(4))
    except Exception: return None
    pair, direction, entry = m.group(1).strip(), ("UP" if "UP" in m.group(2).upper() else "DOWN"), m.group(3).strip()
    return (pair, direction, entry, expiry) if pair and entry and expiry in EXPIRIES else None

def base_url():
    return os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/") or BASE

def mode_url(cid, value):
    raw = f"{int(cid)}|{value}|{int(time.time())//600}"
    return base_url() + "/trade/mode?" + urlencode({"token": pack(raw)})

def trade_url(cid, pair, direction, entry, expiry):
    raw = f"{int(cid)}|{pair}|{direction}|{entry}|{expiry}|{int(time.time())//300}"
    return base_url() + "/trade/select?" + urlencode({"token": pack(raw)})

def mode_markup(cid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🧪 DEMO", url=mode_url(cid, "DEMO")), InlineKeyboardButton("🔴 REAL", url=mode_url(cid, "REAL"))]])

def signal_markup(cid, text):
    d = parse_signal(text)
    if not d or mode(cid) not in ("DEMO", "REAL"): return None
    p, direction, entry, expiry = d
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚡ TRADE NOW", url=trade_url(cid, p, direction, entry, expiry))]])

def start_scan(cid):
    a = app()
    loop = getattr(a, "runtime_loop", None) if a else None
    application = getattr(a, "telegram_application", None) if a else None
    if not a or not loop or not application or mode(cid) not in ("DEMO", "REAL"): return False
    current = getattr(a, "manual_scan_task", None)
    if current is not None and not current.done(): return True
    def kick():
        current = getattr(a, "manual_scan_task", None)
        if current is None or current.done():
            try:
                import asyncio
                a.manual_scan_task = asyncio.create_task(a.scan_cycle(application), name="manual-scan")
            except Exception as e:
                a.log.warning("FINAL FLOW scan start failed: %s", e)
    loop.call_soon_threadsafe(kick)
    return True

def patch_start(a):
    global START_PATCHED
    application = getattr(a, "telegram_application", None)
    if not application: return False
    for hs in getattr(application, "handlers", {}).values():
        for h in hs:
            if "start" not in getattr(h, "commands", set()): continue
            if getattr(h.callback, "_FINAL_START", False): START_PATCHED=True; return True
            async def final_start(update, context):
                user=getattr(update,"effective_user",None); chat=getattr(update,"effective_chat",None)
                uid=getattr(user,"id",getattr(chat,"id",None)); cid=getattr(chat,"id",None)
                if cid is None: return
                try: a.remember_chat(update)
                except Exception: pass
                if uid not in authorized_ids(a):
                    await update.message.reply_text("🔐 ACCESS REQUIRED\n\nSend /access YOUR_ACCESS_CODE first, then /start again.")
                    return
                if mode(uid) not in ("DEMO","REAL"):
                    await update.message.reply_text(
                        "🎯 PRIYANITHAN AI TRADING\n\nACCESS: VERIFIED ✅\nChoose DEMO or REAL before the scan.\n🤖 AI confirmation is required.\n⏱️ Signals run every 5 minutes.\n⚠️ AUTO TRADE: OFF — manual trade only.",
                        reply_markup=mode_markup(uid),
                    )
                    return
                await update.message.reply_text(f"✅ ACCESS OK | MODE: {mode(uid)}\n🔎 Fresh scan started.\n🤖 AI confirmation required.\n⏱️ Next signal cycle: every 5 minutes.")
                start_scan(uid)
            final_start._FINAL_START=True
            h.callback=final_start
            START_PATCHED=True
            a.log.info("FINAL FLOW: /start -> access -> DEMO/REAL -> scan")
            return True
    return False

def install_routes(a):
    flask=getattr(a,"app",None)
    if flask is None: return
    rules={r.rule for r in flask.url_map.iter_rules()}
    if "/trade/mode" not in rules:
        def trade_mode():
            from flask import request
            raw=unpack(request.args.get("token",""))
            if not raw: return "Invalid mode link",403
            parts=raw.split("|")
            if len(parts)!=3 or parts[1] not in ("DEMO","REAL"): return "Invalid mode link",400
            try: cid=int(parts[0]); bucket=int(parts[2])
            except: return "Invalid mode link",400
            now=int(time.time())//600
            if abs(bucket-now)>1: return "Mode link expired. Send /start again.",410
            if cid not in authorized_ids(a): return "Access not authorized. Send /access first.",403
            value=parts[1]; set_mode(cid,value)
            appx=getattr(a,"telegram_application",None)
            if appx is not None:
                async def notify():
                    await appx.bot.send_message(chat_id=cid,text=f"✅ MODE SELECTED: {value}\n🔎 Fresh scan started.\n🤖 AI confirmation required.\n⏱️ Signals every 5 minutes.\n⚠️ AUTO TRADE: OFF — manual trade only.")
                try:
                    import asyncio
                    asyncio.run_coroutine_threadsafe(notify(),a.runtime_loop)
                except Exception: pass
            start_scan(cid)
            return f"<meta name='viewport' content='width=device-width,initial-scale=1'><body style='font-family:system-ui;padding:28px'><h2>{'🧪' if value=='DEMO' else '🔴'} {value} MODE SELECTED</h2><p>Fresh scan started.</p><p>AI confirmation → qualified signal → TRADE NOW.</p><b>AUTO TRADE: OFF</b></body>"
        flask.add_url_rule("/trade/mode",endpoint="final_trade_mode",view_func=trade_mode)
    if "/trade/select" not in {r.rule for r in flask.url_map.iter_rules()}:
        def trade_select():
            from flask import request
            raw=unpack(request.args.get("token",""))
            if not raw: return "Invalid trade link",403
            parts=raw.split("|")
            if len(parts)!=6: return "Invalid trade link",400
            cid,pair,direction,entry,expiry_s,bucket_s=parts
            try: cid=int(cid); expiry=int(expiry_s); bucket=int(bucket_s)
            except: return "Invalid trade link",400
            if direction not in ("UP","DOWN") or expiry not in EXPIRIES: return "Invalid trade signal",400
            if abs(bucket-int(time.time())//300)>1: return "Trade link expired. Wait for the next signal.",410
            value=mode(cid)
            if value not in ("DEMO","REAL"): return "Select DEMO or REAL from /start first.",403
            url=DEMO_URL if value=="DEMO" else REAL_URL
            return f"""<meta name="viewport" content="width=device-width,initial-scale=1"><style>body{{font-family:system-ui;background:#111827;color:white;padding:22px}}.c{{max-width:520px;margin:auto;background:#1f2937;padding:24px;border-radius:18px}}.o{{display:block;background:#166534;color:white;padding:17px;border-radius:14px;text-align:center;text-decoration:none;font-weight:900;margin-top:20px}}</style><div class="c"><h2>⚡ TRADE NOW</h2><p>Mode: <b>{html.escape(value)}</b></p><p>Asset: <b>{html.escape(pair)}</b></p><p>Direction: <b>{html.escape(direction)}</b></p><p>Entry reference: <b>{html.escape(entry)}</b></p><p>Expiry: <b>{expiry} MIN</b></p><a class="o" href="{html.escape(url,quote=True)}">OPEN OLYMPTRADE WEB</a><p>⚠️ AUTO TRADE: OFF. Verify the exact asset, current price, direction and expiry, then place the trade manually.</p></div>"""
        flask.add_url_rule("/trade/select",endpoint="final_trade_select",view_func=trade_select)

def patch_senders(a):
    global BOT_PATCHED
    original=getattr(Bot,"send_message",None)
    if original and not getattr(original,"_FINAL_BOT_SEND",False):
        async def final_send(self,*args,**kwargs):
            text=kwargs.get("text", args[1] if len(args)>=2 else "")
            d=parse_signal(text)
            if d:
                cid=kwargs.get("chat_id", args[0] if args else None)
                if mode(cid) not in ("DEMO","REAL"): return None
                if kwargs.get("reply_markup") is None: kwargs["reply_markup"]=signal_markup(cid,text)
            return await original(self,*args,**kwargs)
        final_send._FINAL_BOT_SEND=True
        Bot.send_message=final_send
    BOT_PATCHED=True
    current=getattr(a,"send_to_recipients",None)
    if current and not getattr(current,"_FINAL_RECIPIENTS",False):
        async def final_recipients(bot,text):
            d=parse_signal(text)
            if not d: return await current(bot,text)
            sent=False
            for cid in a.recipients():
                if mode(cid) not in ("DEMO","REAL"): continue
                try:
                    await bot.send_message(chat_id=cid,text=text,reply_markup=signal_markup(cid,text)); sent=True
                except Exception as e: a.log.warning("FINAL signal send failed %s: %s",cid,e)
            return sent
        final_recipients._FINAL_RECIPIENTS=True
        a.send_to_recipients=final_recipients

def install():
    global PATCHED
    a=app()
    if not a: return False
    install_routes(a); patch_senders(a); patch_start(a); PATCHED=True
    return True

try: install()
except Exception: pass

def bootstrap():
    for _ in range(1800):
        try: install()
        except Exception:
            a=app()
            if a and hasattr(a,"log"): a.log.exception("FINAL FLOW bootstrap failed")
        time.sleep(1)

threading.Thread(target=bootstrap,name="priyanithan-final-flow",daemon=True).start()
