from __future__ import annotations
import io
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 900, 1200
BG = (2, 8, 16, 255)
WHITE = (242, 248, 255)
MUTED = (155, 185, 210)
GREEN = (65, 255, 145)
CYAN = (35, 205, 255)
GOLD = (255, 205, 70)
RED = (255, 75, 90)
PURPLE = (190, 105, 255)
ORANGE = (255, 165, 55)


def font(n, bold=False):
    p = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    return ImageFont.truetype(p, n)


def glow(im, xy, radius, rgb, alpha=90):
    layer = Image.new('RGBA', im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = xy
    for r in range(radius, 8, -10):
        a = max(0, int(alpha * (1 - r / radius) ** 1.5))
        d.ellipse((x-r, y-r, x+r, y+r), fill=(*rgb, a))
    im.alpha_composite(layer.filter(ImageFilter.GaussianBlur(16)))


def _text(d, xy, text, size, fill=WHITE, bold=False, anchor='la'):
    d.text(xy, str(text), font=font(size, bold), fill=fill, anchor=anchor)


def _panel(d, box, title, accent=CYAN):
    d.rounded_rectangle(box, 22, fill=(5, 18, 31, 245), outline=(*accent, 210), width=3)
    x1, y1, x2, _ = box
    d.rounded_rectangle((x1+2, y1+2, x2-2, y1+62), 20, fill=(*accent, 28), outline=(*accent, 80), width=1)
    _text(d, (x1+24, y1+31), title, 24, accent, True, 'lm')


def _kv(d, x, y, label, value, value_fill=WHITE, size=20):
    _text(d, (x, y), label, size, MUTED, False, 'lm')
    _text(d, (x+245, y), value, size, value_fill, True, 'lm')


def card(kind='selected', asset='ASIA_X', direction='', confidence=0, expiry=0, reason='', **kwargs):
    im = Image.new('RGBA', (W, H), BG)
    glow(im, (120, 160), 260, (0, 110, 255), 70)
    glow(im, (790, 870), 300, (0, 230, 140), 75)
    glow(im, (450, 1150), 260, (255, 180, 30), 45)
    d = ImageDraw.Draw(im, 'RGBA')
    d.rounded_rectangle((14, 14, W-14, H-14), 30, fill=(3, 12, 24, 248), outline=(*CYAN, 210), width=4)

    # Persistent brand header: every image uses the same CANDICE logo area.
    d.rounded_rectangle((30, 30, W-30, 176), 26, fill=(5, 16, 29, 250), outline=(*GOLD, 190), width=3)
    d.ellipse((54, 54, 148, 148), fill=(5, 27, 45), outline=(*GOLD, 220), width=4)
    d.arc((68, 68, 134, 134), 25, 330, fill=GOLD, width=8)
    d.line((99, 111, 125, 88), fill=GREEN, width=7)
    d.polygon([(125,88), (114,90), (124,101)], fill=GREEN)
    _text(d, (170, 78), 'CANDICE', 48, WHITE, True, 'lm')
    _text(d, (171, 119), 'AI TRADING BOT', 22, GOLD, True, 'lm')
    _text(d, (W-58, 64), 'LIVE MARKET', 18, GREEN, True, 'ra')
    _text(d, (W-58, 96), 'FLEX / FIXED-TIME', 17, CYAN, True, 'ra')
    _text(d, (W-58, 129), 'MANUAL ONLY', 17, WHITE, True, 'ra')
    d.line((170, 150, W-55, 150), fill=(*CYAN, 120), width=2)

    title_map = {
        'selected': ('1  •  ASSET SELECTED', GREEN),
        'signal': ('2  •  AI SIGNAL', CYAN),
        'result': ('3  •  TRADE RESULT', PURPLE),
        'session': ('4  •  SESSION TIME', ORANGE),
        'expiry': ('5  •  EXPIRY REACHED', RED),
        'recovery': ('6  •  RECOVERY TIME', GREEN),
        'status': ('SYSTEM STATUS', CYAN),
    }
    title, accent = title_map.get(kind, ('CANDICE UPDATE', CYAN))
    _panel(d, (30, 198, W-30, 1030), title, accent)

    # Shared asset/status block.
    _text(d, (60, 280), asset, 48, WHITE, True, 'lm')
    d.rounded_rectangle((650, 250, 840, 298), 20, fill=(35, 25, 4), outline=GOLD, width=2)
    _text(d, (745, 274), 'OTC • FLEX', 18, GOLD, True, 'mm')

    if kind == 'selected':
        rows = [
            ('✓', 'Fresh 1-minute candle', 'VERIFIED', GREEN),
            ('◎', 'Candice AI analysis', 'ON', CYAN),
            ('◷', 'Next signal window', 'NEXT 5 MIN', WHITE),
            ('⏱', 'AI duration', '2 / 3 / 5 / 10 / 15 MIN', WHITE),
            ('◉', 'Selection', 'CONFIRMED', GREEN),
        ]
        y = 350
        for icon, label, value, col in rows:
            _text(d, (70, y), icon, 27, col, True, 'lm')
            _text(d, (125, y), label, 21, MUTED, False, 'lm')
            _text(d, (810, y), value, 21, col, True, 'ra')
            d.line((65, y+28, 835, y+28), fill=(35, 75, 105, 90), width=1)
            y += 88
        _text(d, (450, 830), 'ASSET READY FOR CANDICE RESEARCH', 24, GREEN, True, 'mm')
        _text(d, (450, 875), 'Research continues while this asset rests.', 19, MUTED, False, 'mm')

    elif kind == 'signal':
        _text(d, (60, 355), direction or 'WAIT', 72, GREEN if direction == 'UP' else RED if direction == 'DOWN' else WHITE, True, 'lm')
        _kv(d, 60, 455, 'Entry', kwargs.get('entry', '—'), WHITE, 22)
        _kv(d, 60, 505, 'Duration', f'{expiry} MIN', WHITE, 22)
        _kv(d, 60, 555, 'Candice AI', f'APPROVED • {confidence}%', GREEN, 22)
        _kv(d, 60, 605, 'Technical', kwargs.get('technical', '—'), CYAN, 22)
        _kv(d, 60, 655, 'Trend', kwargs.get('trend', direction or '—'), GREEN if kwargs.get('trend', direction) == 'UP' else RED, 22)
        _text(d, (60, 735), 'ANALYSIS', 20, GOLD, True, 'lm')
        _text(d, (60, 775), (reason or 'Fresh aligned market evidence.')[:78], 19, WHITE, False, 'lm')
        _text(d, (60, 810), (reason or '')[78:156], 19, WHITE, False, 'lm')
        _text(d, (450, 895), f'EXPIRY  •  {kwargs.get("expiry_time", "VERIFYING")}', 25, ORANGE, True, 'mm')

    elif kind == 'result':
        result = direction or 'UNRESOLVED'
        col = GREEN if result == 'WIN' else RED if result == 'LOSS' else ORANGE
        _text(d, (450, 370), result, 76, col, True, 'mm')
        _kv(d, 70, 475, 'Entry', kwargs.get('entry', '—'), WHITE, 22)
        _kv(d, 70, 525, 'Expiry', kwargs.get('exit', '—'), WHITE, 22)
        _kv(d, 70, 575, 'Duration', f'{expiry} MIN', WHITE, 22)
        _kv(d, 70, 625, 'Verification', kwargs.get('verification', 'candle-closed'), GREEN, 22)
        _kv(d, 70, 675, 'Result Time', kwargs.get('result_time', '—'), CYAN, 22)
        _text(d, (450, 795), 'MARKET OUTCOME • NOT BROKER ACCOUNT P/L', 20, MUTED, True, 'mm')

    elif kind == 'session':
        _kv(d, 60, 380, 'Session Start', kwargs.get('session_start', '—'), GREEN, 22)
        _kv(d, 60, 450, 'Session End', kwargs.get('session_end', '—'), RED, 22)
        _kv(d, 60, 520, 'Next Session', kwargs.get('next_session', '—'), CYAN, 22)
        _kv(d, 60, 590, 'Signal Countdown', kwargs.get('countdown', '—'), GREEN, 22)
        _text(d, (450, 720), 'SIGNAL SESSION ACTIVE' if kwargs.get('active', False) else 'RESEARCH ONLY', 32, GREEN if kwargs.get('active', False) else ORANGE, True, 'mm')

    elif kind == 'expiry':
        _text(d, (450, 370), 'EXPIRY REACHED', 50, RED, True, 'mm')
        _kv(d, 60, 470, 'Direction', direction or '—', RED if direction == 'DOWN' else GREEN, 22)
        _kv(d, 60, 525, 'Entry', kwargs.get('entry', '—'), WHITE, 22)
        _kv(d, 60, 580, 'Duration', f'{expiry} MIN', WHITE, 22)
        _kv(d, 60, 635, 'Expiry Boundary', kwargs.get('expiry_time', '—'), ORANGE, 22)
        _text(d, (450, 760), 'VERIFYING EXACT EXPIRY CANDLE…', 25, ORANGE, True, 'mm')
        _text(d, (450, 810), 'Please wait for verified WIN / LOSS.', 20, MUTED, False, 'mm')

    elif kind == 'recovery':
        _kv(d, 60, 390, 'After Loss Recovery', kwargs.get('recovery', '15 MIN'), GREEN, 22)
        _kv(d, 60, 460, 'Quick Recovery', kwargs.get('quick_recovery', '5 MIN'), ORANGE, 22)
        _kv(d, 60, 530, 'Next Signal', kwargs.get('next_signal', 'Automatic'), CYAN, 22)
        _kv(d, 60, 600, 'Daily Loss Limit', kwargs.get('daily_limit', 'ACTIVE'), GREEN, 22)
        _kv(d, 60, 670, '3-Loss Stop', kwargs.get('streak_stop', 'ACTIVE'), GREEN, 22)
        _text(d, (450, 790), 'SYSTEM PROTECTED', 34, GREEN, True, 'mm')

    else:
        _text(d, (450, 390), 'CANDICE AI', 52, GREEN, True, 'mm')
        _text(d, (450, 470), 'LIVE MARKET • AI ANALYSIS', 25, CYAN, True, 'mm')
        _text(d, (450, 560), kwargs.get('status_text', 'System online.'), 22, WHITE, False, 'mm')

    # Persistent safety footer: always visible in every update image.
    d.rounded_rectangle((30, 900, W-30, 970), 18, fill=(7, 15, 25), outline=(*RED, 120), width=2)
    _text(d, (115, 935), 'FOREX OFF', 20, RED, True, 'mm')
    _text(d, (310, 935), 'AUTO-TRADE OFF', 20, RED, True, 'mm')
    _text(d, (525, 935), 'MARTINGALE OFF', 20, RED, True, 'mm')
    _text(d, (760, 935), 'MANUAL ONLY', 20, GREEN, True, 'mm')

    d.rounded_rectangle((30, 1050, W-30, 1170), 25, fill=(5, 20, 29), outline=(*GOLD, 190), width=3)
    _text(d, (450, 1080), 'CANDICE AI', 34, GOLD, True, 'mm')
    _text(d, (450, 1118), 'MORE THAN A BOT  •  YOUR TRADING PARTNER', 19, WHITE, True, 'mm')
    _text(d, (450, 1148), 'OBSERVE • ANALYZE • COMPARE • LEARN • DECIDE • MONITOR • IMPROVE', 14, GREEN, True, 'mm')
    return im.convert('RGB')


def png_bytes(**kwargs):
    b = io.BytesIO()
    card(**kwargs).save(b, format='PNG', optimize=True)
    b.seek(0)
    b.name = 'candice-update.png'
    return b


def gif_bytes(**kwargs):
    # Kept as a compatibility alias for older callers; Telegram now uses PNG images.
    return png_bytes(**kwargs)


def update_gif_bytes():
    # Compatibility only. The bot no longer sends a separate update image.
    return png_bytes(kind='status', status_text='CANDICE v8 • system update ready')
