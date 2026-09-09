from pathlib import Path

p = Path("sitecustomize.py")
s = p.read_text(encoding="utf-8")
marker = "# FAST MANUAL ENTRY UI BOOTSTRAP\n"
if marker not in s:
    s += "\n" + marker
    s += "try:\n"
    s += "    import fast_manual_entry  # noqa: F401\n"
    s += "except Exception:\n"
    s += "    import logging as _fme_logging\n"
    s += "    _fme_logging.getLogger(\"priyanithan\").exception(\"FAST MANUAL ENTRY IMPORT FAILED\")\n"
    p.write_text(s, encoding="utf-8")
    print("FAST MANUAL ENTRY bootstrap appended")
else:
    print("FAST MANUAL ENTRY bootstrap already present")
