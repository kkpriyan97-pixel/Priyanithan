"""Overlay: consume explicit account-info selection markers without guessing."""
from __future__ import annotations
import account_telemetry as at

def _rows(feed):
    c=getattr(feed,"client",None); meta=getattr(c,"candice_account_metadata",None) if c else None; out=[]
    if isinstance(meta,dict):
        for group,payload in meta.items():
            d=payload.get("d") if isinstance(payload,dict) else None
            if isinstance(d,list):
                for r in d:
                    if isinstance(r,dict):
                        x=dict(r); x.setdefault("group",group); out.append(x)
    return out

def _find(rows,feed,raw):
    base=at._find_selected_original(rows,feed,raw)
    if base[0] is not None:return base
    meta=_rows(feed)
    marked=[r for r in meta if at._explicit_selected(r) and at._row_mode(r) in {"demo","real"}]
    if len(marked)!=1:return base
    wanted=at._row_account_id(marked[0])
    matches=[r for r in rows if at._row_account_id(r)==wanted and at._balance(r) is not None]
    if len(matches)==1:return matches[0],"account_info_selected_marker"
    return base

if not hasattr(at,"_find_selected_original"):
    at._find_selected_original=at._find_selected
    at._find_selected=_find
