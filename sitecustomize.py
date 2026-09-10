"""Intentionally empty runtime customization.

The application is now self-contained in app.py. Legacy import hooks and
hotfix bootstraps are disabled so they cannot add old Telegram handlers or
rewrite the fresh main engine at startup.
"""
