from __future__ import annotations
import re

_OTC_RE = re.compile(r"(?:^|[_./-])(?:OTC|OTC\b)", re.I)
_FLEX_RE = re.compile(r"(?:^|[_./-])FLEX(?:$|[_./-])", re.I)

def explicit_market(value):
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if _OTC_RE.search(s):
        return "OTC"
    if _FLEX_RE.search(s):
        return "FLEX"
    return None

def extract_explicit_market(node):
    if isinstance(node, str):
        return explicit_market(node)
    if isinstance(node, dict):
        for key in ("pair","symbol","asset","instrument","ticker","asset_id","assetId","symbolId","name","display_name","title","code"):
            if key in node:
                m = extract_explicit_market(node[key])
                if m:
                    return m
        for value in node.values():
            m = extract_explicit_market(value)
            if m:
                return m
    elif isinstance(node, list):
        for value in node:
            m = extract_explicit_market(value)
            if m:
                return m
    return None
