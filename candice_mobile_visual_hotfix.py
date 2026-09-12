"""Candice mobile Telegram visual hotfix.

Keeps the approved 3D card design, but delivers it as a compact mobile-first
GIF so Telegram users can read the full card without opening/zooming it.
Trading logic is untouched: no orders, no martingale, no forced signals.
"""
from __future__ import annotations

import io
import sys
import threading
import time

from PIL import Image

PATCHED = False
TARGET_WIDTH = 720
TARGET_HEIGHT = 900


def _visual_module():
    return sys.modules.get("candice_visual_cards_hotfix")


def _compact_gif(original_builder, image):
    # Preserve the approved proportions exactly: 1024x1280 -> 720x900.
    compact = image.convert("RGB").resize(
        (TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS
    )

    # Reuse the existing animation engine, then compact every GIF frame.
    raw = original_builder(compact, frames=8, duration=180)
    raw.seek(0)
    src = Image.open(raw)
    frames = []
    try:
        for i in range(getattr(src, "n_frames", 1)):
            src.seek(i)
            frames.append(src.convert("RGB").copy())
    except EOFError:
        pass
    finally:
        src.close()

    if not frames:
        frames = [compact]

    out = io.BytesIO()
    frames[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=180,
        loop=0,
        optimize=True,
        disposal=2,
    )
    out.seek(0)
    return out


def _install():
    global PATCHED
    m = _visual_module()
    if m is None or not callable(getattr(m, "_animated_bytes", None)):
        return False
    if getattr(m, "_CANDICE_MOBILE_VISUAL", False):
        PATCHED = True
        return True

    original = m._animated_bytes

    def mobile_animated_bytes(image, frames=10, duration=160):
        if image is None:
            return None
        return _compact_gif(original, image)

    m._animated_bytes = mobile_animated_bytes
    m._CANDICE_MOBILE_VISUAL = True
    if hasattr(m, "log"):
        try:
            m.log.warning(
                "CANDICE MOBILE VISUAL ACTIVE: 720x900 compact 3D Telegram cards"
            )
        except Exception:
            pass
    PATCHED = True
    return True


def _boot():
    for _ in range(300):
        try:
            if _install():
                return
        except Exception:
            pass
        time.sleep(0.2)


threading.Thread(target=_boot, name="candice-mobile-visual", daemon=True).start()
