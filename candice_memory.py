from __future__ import annotations
import json, os, threading
from pathlib import Path
from datetime import datetime, timezone

PATH = Path(os.getenv('CANDICE_MEMORY_FILE', 'data/candice_memory.json'))
LOCK = threading.Lock()
MAX = max(5000, int(os.getenv('CANDICE_MEMORY_MAX_EVENTS', '12000')))


def _load():
    try:
        x = json.loads(PATH.read_text())
        return x if isinstance(x, list) else []
    except Exception:
        return []


def record(event):
    """Persist an event without storing secrets or credentials."""
    if not isinstance(event, dict):
        return
    PATH.parent.mkdir(parents=True, exist_ok=True)
    safe = dict(event)
    for key in ('token', 'api_key', 'access_token', 'password', 'secret'):
        safe.pop(key, None)
    with LOCK:
        x = _load()
        x.append(safe)
        PATH.write_text(json.dumps(x[-MAX:], ensure_ascii=False, separators=(',', ':')))


def recent(asset=None, n=12):
    x = _load()
    if asset:
        x = [e for e in x if e.get('asset') == asset]
    return x[-max(1, int(n)):]


def _result_events(asset=None, limit=1000):
    x = _load()
    if asset:
        x = [e for e in x if e.get('asset') == asset]
    return [e for e in x if str(e.get('result', '')).upper() in ('WIN', 'LOSS')][-limit:]


def _stats(events):
    wins = sum(str(e.get('result', '')).upper() == 'WIN' for e in events)
    losses = sum(str(e.get('result', '')).upper() == 'LOSS' for e in events)
    total = wins + losses
    return {
        'samples': total,
        'wins': wins,
        'losses': losses,
        'win_rate': round(100 * wins / total, 1) if total else None,
    }


def _bucket_stats(events, field):
    out = {}
    for e in events:
        value = e.get(field)
        if value is None or value == '':
            continue
        key = str(value).upper() if isinstance(value, str) else str(value)
        b = out.setdefault(key, {'samples': 0, 'wins': 0, 'losses': 0})
        b['samples'] += 1
        if str(e.get('result', '')).upper() == 'WIN':
            b['wins'] += 1
        elif str(e.get('result', '')).upper() == 'LOSS':
            b['losses'] += 1
    for b in out.values():
        b['win_rate'] = round(100 * b['wins'] / b['samples'], 1) if b['samples'] else None
    return out


def _pattern_stats(events):
    out = {}
    for e in events:
        patterns = e.get('patterns') or e.get('candle_patterns') or ()
        if isinstance(patterns, str):
            patterns = [patterns]
        for p in patterns:
            key = str(p)
            b = out.setdefault(key, {'samples': 0, 'wins': 0, 'losses': 0})
            b['samples'] += 1
            if str(e.get('result', '')).upper() == 'WIN':
                b['wins'] += 1
            elif str(e.get('result', '')).upper() == 'LOSS':
                b['losses'] += 1
    for b in out.values():
        b['win_rate'] = round(100 * b['wins'] / b['samples'], 1) if b['samples'] else None
    return out


def learning_context(asset=None):
    """Return compact, decision-useful historical learning data.

    This is descriptive memory, not a guarantee or an automatic strategy override.
    """
    events = _result_events(asset, 1000)
    recent_events = events[-20:]
    context = {
        'scope': asset or 'ALL_ASSETS',
        'overall': _stats(events),
        'by_direction': _bucket_stats(events, 'direction'),
        'by_expiry': _bucket_stats(events, 'expiry'),
        'by_regime': _bucket_stats(events, 'regime'),
        'by_timeframe': _bucket_stats(events, 'timeframe'),
        'by_session': _bucket_stats(events, 'session'),
        'by_pattern': _pattern_stats(events),
        'recent_outcomes': [
            {
                'ts': e.get('ts'),
                'asset': e.get('asset'),
                'direction': e.get('direction'),
                'expiry': e.get('expiry'),
                'confidence': e.get('confidence'),
                'regime': e.get('regime'),
                'patterns': e.get('patterns') or e.get('candle_patterns') or (),
                'result': e.get('result'),
            }
            for e in recent_events
        ],
    }
    return context


def summary(asset=None):
    events = _result_events(asset, 1000)
    s = _stats(events)
    # Keep the old summary keys for compatibility while adding full learning data.
    s['recent'] = recent(asset, 12)
    s['learning'] = learning_context(asset)
    return s


def today_results(tz=None):
    tz = tz or timezone.utc
    day = datetime.now(tz).date()
    out = []
    for e in _load():
        if str(e.get('result', '')).upper() not in ('WIN', 'LOSS'):
            continue
        try:
            if datetime.fromtimestamp(float(e.get('ts', 0)), tz).date() == day:
                out.append(e)
        except Exception:
            pass
    return out


def today_risk(tz=None):
    x = today_results(tz)
    losses = sum(str(e.get('result', '')).upper() == 'LOSS' for e in x)
    streak = 0
    for e in reversed(x):
        if str(e.get('result', '')).upper() == 'LOSS':
            streak += 1
        else:
            break
    return {'losses': losses, 'streak': streak, 'results': len(x)}


def authorized_users():
    """Load authorized Telegram user IDs from the persistent memory file."""
    users = set()
    for e in _load():
        if e.get('type') == 'authorized_user':
            try:
                users.add(int(e.get('user_id')))
            except Exception:
                pass
    return users


def authorize_user(user_id):
    """Persist a Telegram user authorization without storing access codes."""
    uid = int(user_id)
    if uid not in authorized_users():
        record({'ts': datetime.now(timezone.utc).timestamp(), 'type': 'authorized_user', 'user_id': uid})
    return uid
