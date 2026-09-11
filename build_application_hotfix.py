"""Early compatibility shim for the Telegram Application builder.

The Render port fix commit exposed a missing build_application symbol in the
fresh app.py. Define the exact native Telegram handler factory in builtins so
app.main_async can resolve it before any asynchronous hotfix thread races.
This module does not enable broker trading.
"""
import builtins


def build_application():
    import app
    application = app.Application.builder().token(app.TELEGRAM_BOT_TOKEN).updater(None).build()
    application.add_handler(app.CommandHandler("start", app.start_cmd))
    application.add_handler(app.CommandHandler("access", app.access_cmd))
    application.add_handler(app.CommandHandler("assets", app.assets_cmd))
    application.add_handler(app.CallbackQueryHandler(app.assets_callback, pattern=r"^(asset:|assets:)"))
    return application


builtins.build_application = build_application
