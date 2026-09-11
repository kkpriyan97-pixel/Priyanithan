"""Candice animated Telegram cards.

Important UI events are sent as real Telegram animations (GIF), not static PNG
photos. Trading logic is untouched: no orders, no martingale, no forced signals.
"""
from __future__ import annotations

import asyncio
import io
import math
import os
import re
import sys
import threading
import time
from PIL import Image, ImageDraw, ImageFont, ImageFilter

PATCHED = False
CAPTION = "Candice AI • Live Market • Manual Trade Only"


def _app():
    m = sys.modules.get("__main__")
    if m is not None and getattr(m, "__file__", "").endswith("app.py"):
        return m
    return sys.modules.get("app")


def _font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


FONT_TITLE = _font(58, True)
FONT_MED = _font(40, True)
FONT_BIG = _font(92, True)
FONT_BODY = _font(30, False)
FONT_SMALL = _font(25, False)
FONT_TINY = _font(21, False)


def _gradient(size, top=(5, 17, 40), bottom=(1, 3, 12)):
    w, h = size
    im = Image.new("RGB", size)
    px = im.load()
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        for x in range(w):
            px[x, y] = c
    return im


def _glow(base, xy, radius=60, color=(0, 170, 255), alpha=130):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = xy
    for r in range(radius, 5, -5):
        a = int(alpha * (1 - r / radius) ** 1.8)
        d.ellipse((x-r, y-r, x+r, y+r), fill=(*color, a))
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius // 4)))


def _round_rect(d, box, radius, fill, outline=None, width=1):
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _chart(draw, box, bullish=True):
    x1, y1, x2, y2 = box
    n = 11
    step = (x2 - x1) / (n + 1)
    prev = y2 - 30
    for i in range(n):
        cx = x1 + step * (i + 1)
        trend = (-1 if bullish else 1) * (18 + i * 2)
        cy = max(y1 + 20, min(y2 - 20, prev + trend + math.sin(i * 1.7) * 18))
        color = (30, 235, 145) if cy < prev else (255, 85, 105)
        draw.line((cx, cy-38, cx, cy+38), fill=color, width=6)
        draw.rectangle((cx-12, cy-18, cx+12, cy+18), fill=color)
        prev = cy


def _card(kind, title, subtitle="", asset="", lines=None, countdown=None):
    W, H = 1024, 1280
    im = _gradient((W, H)).convert("RGBA")
    _glow(im, (850, 150), 180, (0, 110, 255), 120)
    _glow(im, (120, 1050), 220, (0, 210, 190), 80)
    d = ImageDraw.Draw(im)
    palette = {
        "session": ((0, 220, 255), (0, 255, 150)),
        "signal": ((255, 185, 40), (0, 205, 255)),
        "win": ((90, 255, 90), (255, 205, 45)),
        "loss": ((255, 65, 70), (180, 20, 40)),
        "recovery": ((60, 230, 210), (70, 120, 255)),
        "research": ((160, 100, 255), (0, 210, 255)),
    }
    c1, c2 = palette.get(kind, ((0, 210, 255), (80, 100, 255)))
    _round_rect(d, (35, 35, W-35, H-35), 48, (3, 8, 20, 225), outline=(*c1, 230), width=4)
    _round_rect(d, (55, 55, W-55, H-55), 38, (6, 14, 30, 225), outline=(80, 130, 190, 80), width=2)
    d.text((85, 82), "PRIYANITHAN AI", font=FONT_SMALL, fill=(130, 200, 255))
    d.text((85, 120), title, font=FONT_TITLE, fill=(245, 250, 255))
    if subtitle:
        d.text((87, 195), subtitle, font=FONT_MED, fill=(*c1, 255))
    cx, cy = 850, 145
    for r, a in [(92, 20), (78, 35), (64, 55)]:
        d.ellipse((cx-r, cy-r, cx+r, cy+r), outline=(*c1, a), width=5)
    d.ellipse((cx-28, cy-28, cx+28, cy+28), fill=(*c1, 220))
    y = 275
    if countdown:
        cx, cy = 512, 430
        for r, col in [(190, c2), (165, c1), (140, (20, 60, 100))]:
            d.ellipse((cx-r, cy-r, cx+r, cy+r), outline=(*col, 230), width=8)
        d.text((cx, cy-25), countdown, font=FONT_BIG, fill=(250, 250, 255), anchor="mm")
        d.text((cx, cy+62), "TIME REMAINING", font=FONT_SMALL, fill=(160, 210, 255), anchor="mm")
        y = 650
    if asset:
        _round_rect(d, (75, y, W-75, y+120), 28, (10, 30, 55, 240), outline=(*c2, 180), width=3)
        d.text((105, y+60), asset, font=FONT_BIG, fill=(255, 210, 75), anchor="lm")
        if kind in ("signal", "loss", "win"):
            _chart(d, (570, y+20, 940, y+100), bullish=kind != "loss")
        y += 155
    if lines:
        for line in lines:
            _round_rect(d, (75, y, W-75, y+76), 22, (7, 22, 42, 210), outline=(50, 110, 170, 100), width=2)
            d.text((100, y+38), str(line), font=FONT_BODY, fill=(235, 242, 250), anchor="lm")
            y += 92
    if kind == "win":
        d.ellipse((650, 720, 900, 970), fill=(255, 196, 30, 35), outline=(255, 220, 80, 220), width=6)
        d.rectangle((700, 805, 850, 910), fill=(255, 185, 45, 220), outline=(255, 235, 150, 255), width=5)
        d.rectangle((765, 805, 785, 910), fill=(255, 90, 110, 230))
        d.polygon([(675, 790), (875, 790), (835, 750), (715, 750)], fill=(255, 205, 55, 220))
        d.text((512, 850), "WIN", font=FONT_BIG, fill=(80, 255, 105), anchor="mm")
    elif kind == "loss":
        d.line((650, 900, 730, 820, 790, 875, 870, 760), fill=(255, 60, 70), width=16)
        d.polygon([(870, 760), (820, 770), (855, 810)], fill=(255, 60, 70))
        d.text((512, 850), "LOSS", font=FONT_BIG, fill=(255, 80, 90), anchor="mm")
    elif kind == "signal":
        d.text((512, 850), "AI SIGNAL", font=FONT_BIG, fill=(0, 220, 255), anchor="mm")
    elif kind == "recovery":
        d.text((512, 850), "RECOVERY", font=FONT_BIG, fill=(70, 240, 210), anchor="mm")
    d.line((75, H-145, W-75, H-145), fill=(*c1, 100), width=2)
    d.text((W//2, H-105), CAPTION, font=FONT_SMALL, fill=(255, 205, 70), anchor="mm")
    return im.convert("RGB")


def _parse_asset(text):
    m = re.search(r"(?:ASSET SELECTED|TRADE RESULT|AI SIGNAL|LOSS|WIN|RECOVERY)[^\n]*?(?:—|-)\s*([A-Z0-9_]+)", text, re.I)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b([A-Z]{3,8}(?:_OTC)?|ASIA_X)\b", text)
    return m.group(1).upper() if m else "MARKET"


def _visual_kind(text):
    u = text.upper()
    if "WIN CONFIRMED" in u or "RECOVERY WIN" in u:
        return "win"
    if "LOSS CONFIRMED" in u or "RECOVERY LOSS" in u or "LOSS →" in u:
        return "loss"
    if "RECOVERY" in u:
        return "recovery"
    if "RESEARCH ONLY" in u or "RESEARCH INTERVAL" in u:
        return "research"
    if "SIGNAL SESSION" in u:
        return "session"
    if "AI SIGNAL" in u or "PRIYANITHAN AI SIGNAL" in u:
        return "signal"
    return None


def _build(text):
    kind = _visual_kind(text)
    if not kind:
        return None
    asset = _parse_asset(text)
    if kind == "win": title, sub = "WIN CONFIRMED", asset
    elif kind == "loss": title, sub = "LOSS CONFIRMED", asset
    elif kind == "signal": title, sub = "NEW AI SIGNAL", asset
    elif kind == "recovery": title, sub = "SHORT RECOVERY MODE", asset
    elif kind == "research": title, sub = "RESEARCH INTERVAL", asset
    else: title, sub = "SIGNAL SESSION ACTIVE", "CANDICE AI"
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("⚠️") or s in (asset, "WIN", "LOSS"):
            continue
        if len(s) > 58:
            s = s[:55] + "..."
        if s.startswith(("💰", "⏱", "🏁", "🤖", "📊", "🕐", "🧠", "📡", "🚫", "🔎", "🔒", "🚀", "🟢", "🔬", "⏳")):
            lines.append(s)
        if len(lines) >= 6:
            break
    countdown = None
    m = re.search(r"(?:SIGNAL|RESEARCH) COUNTDOWN:\s*([0-9:]+)", text, re.I)
    if m:
        countdown = m.group(1)
    return _card(kind, title, sub, asset if kind not in ("session", "research") else "", lines, countdown)


def _animated_bytes(image, frames=10, duration=160):
    """Create a real looping GIF with visible pulse/scan animation."""
    frames_out = []
    W, H = image.size
    for i in range(frames):
        f = image.copy().convert("RGB")
        d = ImageDraw.Draw(f, "RGBA")
        phase = i / max(1, frames - 1)
        # Pulsing header orb / border highlight.
        r = int(48 + 20 * math.sin(phase * math.tau))
        d.ellipse((850-r, 145-r, 850+r, 145+r), outline=(0, 220, 255, 170), width=6)
        # Moving scan beam makes the animation unmistakable.
        sy = 75 + int((H - 180) * ((i % frames) / frames))
        d.line((75, sy, W-75, sy), fill=(0, 220, 255, 80), width=3)
        # Small status pulse.
        rr = 8 + int(5 * (1 + math.sin(phase * math.tau)))
        d.ellipse((92-rr, 1070-rr, 92+rr, 1070+rr), fill=(0, 255, 170, 190))
        frames_out.append(f)
    out = io.BytesIO()
    frames_out[0].save(out, format="GIF", save_all=True, append_images=frames_out[1:], duration=duration, loop=0, optimize=True)
    out.seek(0)
    return out


def animated_card_bytes(text=None, image=None):
    if image is None:
        image = _build(str(text or ""))
    if image is None:
        return None
    return _animated_bytes(image)


async def _send_card(bot, uid, text):
    image = _build(text)
    if image is None:
        return False
    buf = _animated_bytes(image)
    await bot.send_animation(chat_id=uid, animation=buf, caption=CAPTION, width=image.width, height=image.height)
    return True


def _install_send_text(a):
    if getattr(a, "_CANDICE_VISUAL_SEND_TEXT", False):
        return
    original = a.send_text
    async def visual_send_text(bot, text, uid):
        try:
            if await _send_card(bot, int(uid), str(text)):
                return
        except Exception as exc:
            a.log.warning("VISUAL ANIMATION FALLBACK: %s", exc)
        return await original(bot, text, uid)
    a._CANDICE_VISUAL_ORIGINAL_SEND_TEXT = original
    a.send_text = visual_send_text
    a._CANDICE_VISUAL_SEND_TEXT = True


def _install():
    global PATCHED
    a = _app()
    if a is None or not callable(getattr(a, "send_text", None)):
        return False
    _install_send_text(a)
    a.log.warning("CANDICE VISUAL ANIMATIONS ACTIVE: real Telegram GIF cards")
    PATCHED = True
    return True


def _boot():
    global PATCHED
    for _ in range(1800):
        try:
            if _install():
                return
        except Exception:
            pass
        time.sleep(0.2)

threading.Thread(target=_boot, name="candice-visual-animations", daemon=True).start()
