from setuptools import setup
import os
import sys
import sysconfig

site_packages = os.path.relpath(sysconfig.get_path("purelib"), sys.prefix)

setup(
    name="candice-runtime-hooks",
    version="1.0.9",
    py_modules=[
        "usercustomize",
        "freshness_patch",
        "telegram_token_only",
        "outcome_recovery_guard",
        "account_telemetry",
        "account_selection_overlay",
        "telegram_response_patch",
        "freshness_delivery_fix",
    ],
    data_files=[(site_packages, ["candice_boot.pth"])],
)
