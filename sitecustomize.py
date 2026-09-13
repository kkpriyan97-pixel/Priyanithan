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


def _install_layers(app):
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
        from candice_ai_fallback import install as install_ai_fallback
        install_ai_fallback(app)
    except Exception as exc:
        print('CANDICE AI FALLBACK FAILED', repr(exc))
    try:
        from candice_zai_provider import install as install_zai_provider
        install_zai_provider(app)
    except Exception as exc:
        print('CANDICE Z.AI PROVIDER FAILED', repr(exc))
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
        from candice_ready_timer import install as install_ready_timer
        install_ready_timer(app)
    except Exception as exc:
        print('CANDICE READY TIMER FAILED', repr(exc))
    try:
        from candice_card_override import install as install_card
        install_card(app)
    except Exception as exc:
        print('CANDICE CARD OVERRIDE FAILED', repr(exc))
    try:
        from candice_strategy_v2 import build_plan
        app._candice_strategy_v2_builder = build_plan
        app._candice_strategy_v2 = True
        print('CANDICE STRATEGY V2 ACTIVE — PATTERN → NEXT CANDLE → SITUATION → DURATION')
    except Exception as exc:
        print('CANDICE STRATEGY V2 FAILED', repr(exc))
    try:
        from candice_strategy_v4 import build_plan as build_plan_v4
        app._candice_strategy_v2_builder = build_plan_v4
        app._candice_own_strategy_v4 = True
        print('CANDICE OWN STRATEGY V4 ACTIVE — MEMORY → PROBABILITY → SESSION → NEXT CANDLE GATE')
    except Exception as exc:
        print('CANDICE OWN STRATEGY V4 FAILED', repr(exc))
    print('CANDICE RUNTIME LAYERS READY — TELEGRAM HANDLERS FIRST — AI FALLBACK ACTIVE — Z.AI LAST RESORT — ASSET TIMER LAST')


def _load_runtime():
    for _ in range(300):
        app = sys.modules.get('app') or sys.modules.get('__main__')
        if app is not None and hasattr(app, 'scan_once') and hasattr(app, '_card_base') and _runtime_ready(app):
            _install_layers(app)
            break
        time.sleep(0.05)
    else:
        print('CANDICE RUNTIME PATCH FAILED: Telegram handlers were not ready')
        return

    for _ in range(120):
        time.sleep(0.5)
        try:
            app = sys.modules.get('app') or sys.modules.get('__main__')
            if app is None or not _runtime_ready(app):
                continue
            if not getattr(app, '_candice_ready_timer_v5', False):
                print('CANDICE READY TIMER WATCHDOG: reinstalling missing layer')
                from candice_ready_timer import install as install_ready_timer
                install_ready_timer(app)
        except Exception as exc:
            print('CANDICE READY TIMER WATCHDOG FAILED', repr(exc))


threading.Thread(target=_load_runtime, daemon=True).start()
