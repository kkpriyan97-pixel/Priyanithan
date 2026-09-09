from pathlib import Path

p = Path("sitecustomize.py")
s = p.read_text(encoding="utf-8")

# Permanent fix for the live Render failure:
# sitecustomize.py uses asyncio.create_task() in patched_send(), but did not
# import asyncio. Keep the import explicit at module scope.
if "\nimport asyncio\n" not in s:
    marker = "import sys\n"
    if marker not in s:
        raise SystemExit("sitecustomize.py import anchor not found")
    s = s.replace(marker, marker + "import asyncio\n", 1)
    p.write_text(s, encoding="utf-8")
    print("FIXED: added import asyncio to sitecustomize.py")
else:
    print("OK: asyncio import already present")

compile(s, "sitecustomize.py", "exec")
print("OK: sitecustomize.py compiles")
