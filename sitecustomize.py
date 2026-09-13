import sys
import threading
import time


def _load_runtime():
    for _ in range(160):
        app = sys.modules.get('app') or sys.modules.get('__main__')
        if app is not None and hasattr(app, 'scan_once') and hasattr(app, '_card_base'):
            try:
                from candice_runtime_patch import install
                install(app)
            except Exception as exc:
                print('CANDICE RUNTIME PATCH FAILED', repr(exc))
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
            return
        time.sleep(0.05)


threading.Thread(target=_load_runtime, daemon=True).start()
