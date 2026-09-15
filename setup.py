from setuptools import setup

setup(
    name="candice-runtime-hooks",
    version="1.0.1",
    py_modules=["usercustomize", "freshness_patch"],
    data_files=[("", ["candice_boot.pth"])],
)
