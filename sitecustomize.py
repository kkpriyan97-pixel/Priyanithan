import sys
import threading
import time


def _load_override():
    for _ in range(100):
        app = sys.modules.get('app')
        if app is not None and hasattr(app, '_card_base'):
            try:
                from candice_card_override import install
                install(app)
            except Exception as exc:
                print('CANDICE CARD OVERRIDE FAILED', repr(exc))
            return
        time.sleep(0.05)


threading.Thread(target=_load_override, daemon=True).start()
