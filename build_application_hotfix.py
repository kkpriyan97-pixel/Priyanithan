"""Build the Telegram Application from the single live app.py module.

Render starts app.py as __main__. Importing a second ``app`` module creates a
separate copy of broker state (including ot_client), which makes Telegram see
"OlympTrade not connected" even while the real websocket is connected.
This shim always resolves the running app.py module first.
No broker order execution is enabled.
"""
import builtins
import sys


def _app():
    module = sys.modules.get("__main__")
    if module is not None and getattr(module, "__file__", "").endswith("app.py"):
        return module
    import app
    return app


def build_application():
    app = _app()
    application = app.Application.builder().token(app.TELEGRAM_BOT_TOKEN).updater(None).build()
    application.add_handler(app.CommandHandler("start", app.start_cmd))
    application.add_handler(app.CommandHandler("access", app.access_cmd))
    application.add_handler(app.CommandHandler("assets", app.assets_cmd))
    application.add_handler(app.CallbackQueryHandler(app.assets_callback, pattern=r"^(asset:|assets:)"))
    # Register /session during construction, before webhook processing starts.
    # This avoids a race where a background hotfix thread adds the handler too late.
    try:
        import candice_session_status_hotfix as session_status
        application.add_handler(app.CommandHandler("session", session_status._session_cmd))
    except Exception as exc:
        app.log.warning("CANDICE /session registration unavailable: %s", exc)
    return application


builtins.build_application = build_application
