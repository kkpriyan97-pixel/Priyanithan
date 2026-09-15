from setuptools import setup
import os
import sys
import sysconfig

# Install the bootstrap .pth into the interpreter's real site-packages directory.
# A data_files destination of "" can land outside site-packages on Render, so
# compute the correct prefix-relative site-packages path explicitly.
site_packages = os.path.relpath(sysconfig.get_path("purelib"), sys.prefix)

setup(
    name="candice-runtime-hooks",
    version="1.0.5",
    py_modules=["usercustomize", "freshness_patch", "telegram_token_only", "outcome_recovery_guard", "account_telemetry"],
    data_files=[(site_packages, ["candice_boot.pth"])],
)
