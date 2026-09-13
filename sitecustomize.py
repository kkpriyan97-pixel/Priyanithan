import sys
import threading
import time


def _runtime_ready(app):
    try:
        tg = getattr(app, 'tg_app', None)
        handlers = getattr(tg, 'handlers', {}) if tg is not None else {}
        return bool(getattr(app, 'BOT_LOOP', None)) and bool(handlers)
    except Exception:
        return False


def _load_runtime():
    # IMPORTANT: wait until Telegram Application + handlers are registered.
    # Runtime callback monkey-patching before handler registration caused the
    # ASSET READY callback/timer to race with bot_main().
    for _ in range(300):
        app = sys.modules.get('app') or sys.modules.get('__main__')
        if app is not None and hasattr(app, 'scan_once') and hasattr(app, '_card_base') and _runtime_ready(app):
            break
        time.sleep(0.05)
    else:
        print('CANDICE RUNTIME PATCH FAILED: Telegram handlers were not ready')
        return

    try:
        from candice_telegram_ratefix import install as install_telegram_ratefix
        install_telegram_ratefix(app)
    except Exception as exc:
        print('CANDICE TELEGRAM RATEFIX FAILED', repr(exc))
    try:
        from candice_runtime_patch import install
        install(app)
    except Exception as exc:
        print('CANDICE RUNTIME PATCH FAILED', repr(exc))
    try:
        from candice_ai_247 import install as install_ai_247
        install_ai_247(app)
    except Exception as exc:
        print('CANDICE AI 24/7 FAILED', repr(exc))
    try:
        from candice_checkpoint import install as install_checkpoint
        install_checkpoint(app)
    except Exception as exc:
        print('CANDICE 5M CHECKPOINT FAILED', repr(exc))
    try:
        from candice_recovery_timer import install as install_recovery_timer
        install_recovery_timer(app)
    except Exception as exc:
        print('CANDICE RECOVERY TIMER FAILED', repr(exc))
    try:
        # Must be installed AFTER Telegram handlers exist so the dispatch
        # handler always resolves the patched asset_callback at runtime.
        from candice_ready_timer import install as install_ready_timer
        install_ready_timer(app)
    except Exception as exc:
        print('CANDICE READY TIMER FAILED', repr(exc))
    try:
        from candice_card_override import install as install_card
        install_card(app)
    except Exception as exc:
        print('CANDICE CARD OVERRIDE FAILED', repr(exc))
    print('CANDICE RUNTIME LAYERS READY — TELEGRAM HANDLERS FIRST — ASSET TIMER LAST')


threading.Thread(target=_load_runtime, daemon=True).start()
