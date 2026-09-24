"""Autonomous multilingual M1 research + forward-only candle-learning engine.

Research is isolated from the live Candice technical brain. It:
* discovers public trading material across many languages and additional languages
  detected from page text/scripts;
* targets at least 10,000 unique domains over 15 days, while respecting robots.txt;
* de-duplicates URLs and near-duplicate content;
* extracts methods, triggers, filters, failure modes and technology references;
* treats marketing/profit claims as claims, never as proof;
* learns a next-1-minute direction model only from information available before
  the next candle exists, then validates it when that candle closes;
* keeps research candidates separate from live signal generation and never trades.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import math
import os
import re
import sqlite3
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx

log=logging.getLogger("candice.learning")

LEARNING_DAYS=15
TARGET_SITES=10_000
TARGET_DAILY=math.ceil(TARGET_SITES/LEARNING_DAYS)
MAX_CONCURRENCY=max(2,min(8,int(os.getenv("LEARNING_MAX_CONCURRENCY","6"))))
SEARCH_CONCURRENCY=max(1,min(2,int(os.getenv("LEARNING_SEARCH_CONCURRENCY","2"))))
REQUEST_GAP_SECONDS=max(0.05,float(os.getenv("LEARNING_REQUEST_GAP_SECONDS","0.20")))
REQUEST_TIMEOUT=float(os.getenv("LEARNING_REQUEST_TIMEOUT","8"))
RUN_INTERVAL_SECONDS=max(60,int(os.getenv("LEARNING_RUN_INTERVAL","120")))
MAX_PAGE_BYTES=int(os.getenv("LEARNING_MAX_PAGE_BYTES","800000"))
DB_PATH=os.getenv("LEARNING_DB_PATH","candice_learning.sqlite3")
STATE_JSON=os.getenv("LEARNING_STATE_JSON","candice_learning_state.json")
USER_AGENT=os.getenv("LEARNING_USER_AGENT","CANDICE-M1-ResearchBot/2.0")
DEFAULT_LEARNING_START_UTC="2026-09-21T20:52:37+00:00"

# 24 major language packs + language-agnostic Unicode/script detection.
LANGUAGE_QUERIES={
"en":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"es":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"pt":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"fr":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"de":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"it":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"nl":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"ru":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"uk":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"pl":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"tr":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"ar":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"fa":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"hi":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"bn":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"ur":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"zh":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"ja":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"ko":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"vi":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"th":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"id":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"ms":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"he":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"],
"el":["1 minute anchored VWAP volume profile POC VAH VAL trading","M1 anchored VWAP volume profile backtest false signals"]
}

METHOD_LIBRARY={
"anchored_vwap":["anchored vwap","AVWAP","vwap anchor","anchored volume weighted average price"],
"volume_profile":["volume profile","POC","point of control","VAH","value area high","VAL","value area low","HVN","LVN"]
}

ALL_TERMS=set(t.lower() for vs in METHOD_LIBRARY.values() for t in vs)

QUALITY_HINTS={"cmegroup.com":.92,"sec.gov":1.0,"investor.gov":1.0,"tradingview.com":.68,"oanda.com":.72,"ig.com":.72,
"babypips.com":.58,"forex.com":.70,"fidelity.com":.78,"schwab.com":.78,"investopedia.com":.62}
MARKETING_PATTERNS=re.compile(
r"(90\s*%|95\s*%|99\s*%|100\s*%|guaranteed|guarantee|guaranteed profit|profit every day|sure win|no loss|稳赚|稳赚不赔|"
r"ganancia garantizada|lucro garantido|profit garanti|гарантированн|garantizado|garantido)",re.I)
SEARCH_HOSTS={"google.com","googleusercontent.com","duckduckgo.com","bing.com","search.yahoo.com"}

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts=[]; self.text_parts=[]; self.links=[]
        self._title=False; self._skip=0; self._anchor=None; self._anchor_text=[]
        self._capture={"p","h1","h2","h3","h4","li","article","section","title","td","th"}
    def handle_starttag(self,tag,attrs):
        tag=tag.lower(); attrs=dict(attrs)
        if tag in {"script","style","noscript","svg","canvas"}: self._skip+=1; return
        if tag=="title": self._title=True
        if tag=="a" and not self._skip and attrs.get("href"):
            self._anchor=attrs["href"]; self._anchor_text=[]
    def handle_endtag(self,tag):
        tag=tag.lower()
        if tag in {"script","style","noscript","svg","canvas"}:
            self._skip=max(0,self._skip-1); return
        if tag=="title": self._title=False
        if tag=="a" and self._anchor:
            self.links.append((self._anchor," ".join(self._anchor_text).strip()))
            self._anchor=None; self._anchor_text=[]
    def handle_data(self,data):
        if self._skip:return
        s=" ".join(data.split())
        if not s:return
        if self._title:self.title_parts.append(s)
        if self._anchor is not None:self._anchor_text.append(s)
        self.text_parts.append(s)

def _clean_url(raw,base=None):
    if not raw:return None
    if base:raw=urljoin(base,raw)
    raw=html.unescape(str(raw).strip())
    if raw.startswith("//"):raw="https:"+raw
    p=urlparse(raw)
    if p.scheme not in {"http","https"} or not p.netloc:return None
    host=(p.hostname or "").lower()
    if not host or host=="localhost" or host.endswith(".local"):return None
    q={k:v for k,v in parse_qs(p.query).items() if not k.lower().startswith(("utm_","fbclid","gclid"))}
    query="&".join(f"{k}={quote_plus(v[0])}" for k,v in sorted(q.items()))
    return urlunparse((p.scheme,host,p.path or "/", "",query,""))

def _unwrap_search_href(href):
    href=html.unescape(href or "")
    p=urlparse(href)
    q=parse_qs(p.query)
    for k in ("uddg","url","target","q"):
        v=q.get(k)
        if v and urlparse(v[0]).scheme in {"http","https"}:return v[0]
    return href

def _domain(url):return (urlparse(url).hostname or "").lower().removeprefix("www.")

def _source_quality(domain):
    d=_domain("https://"+domain) if "://" not in domain else _domain(domain)
    if d in QUALITY_HINTS:return QUALITY_HINTS[d]
    if d.endswith(".gov") or d.endswith(".edu") or d.endswith(".ac.uk"):return 1.0
    if d.endswith(".org"):return .76
    if d.endswith(".edu.au"):return 1.0
    return .45

def _script_language(text):
    counts=defaultdict(int)
    for ch in text[:8000]:
        n=unicodedata.name(ch,"")
        if "ARABIC" in n:counts["ar"]+=1
        elif "CYRILLIC" in n:counts["ru"]+=1
        elif "HEBREW" in n:counts["he"]+=1
        elif "GREEK" in n:counts["el"]+=1
        elif "DEVANAGARI" in n:counts["hi"]+=1
        elif "BENGALI" in n:counts["bn"]+=1
        elif "THAI" in n:counts["th"]+=1
        elif "HANGUL" in n:counts["ko"]+=1
        elif any(x in n for x in ("CJK","HIRAGANA","KATAKANA")):counts["zh" if "CJK" in n else "ja"]+=1
    return max(counts,key=counts.get) if counts else "en"

def _normalize_text(text):
    return re.sub(r"\s+"," "," ".join(text.split())).strip()

def _fingerprint(text):
    return hashlib.sha256(_normalize_text(text).lower()[:20000].encode("utf-8","ignore")).hexdigest()

def _simhash(text):
    words=re.findall(r"\w+",_normalize_text(text).lower(),re.UNICODE)
    if not words:return "0"
    vec=[0]*64
    for w in words[:2500]:
        h=int(hashlib.blake2b(w.encode("utf-8","ignore"),digest_size=8).hexdigest(),16)
        for i in range(64):vec[i]+=1 if (h>>i)&1 else -1
    out=sum((1<<i) for i,v in enumerate(vec) if v>=0)
    return f"{out:016x}"

def _hamming(a,b):
    try:return (int(a,16)^int(b,16)).bit_count()
    except Exception:return 64

def _nearby_evidence(text,hits):
    sentences=re.split(r"(?<=[.!?。！？])\s+",text)
    out=[]
    for s in sentences:
        ls=s.lower()
        if any(h in ls for h in hits) and 20<=len(s)<=1200:out.append(s.strip())
    return out[:8]

def _extract_evidence(text,title,url,language):
    low=text.lower(); out=[]
    for method_id,terms in METHOD_LIBRARY.items():
        hits=[t for t in terms if t in low]
        if not hits:continue
        evidence=_nearby_evidence(text,hits)
        if not evidence:continue
        mflag=bool(MARKETING_PATTERNS.search(text))
        specificity=min(1.0,0.15*len(hits)+0.25*int(any(x in low for x in ("1 minute","1-minute","m1","1min","1分","دقيقة واحدة","1 minuto"))))
        score=max(0,min(1,_source_quality(_domain(url))*.65+specificity*.35-(.30 if mflag else 0)))
        claim=re.findall(r"(?:\d{2,3}(?:\.\d+)?\s*%\s*(?:win|accuracy|profit|winrate|taux|rentabilidad|acerto|taxa))",text,re.I)[:5]
        out.append({"method_id":method_id,"title":title[:240],"url":url,"domain":_domain(url),"language":language,
                    "matched_terms":hits[:20],"evidence":evidence,"source_quality":round(_source_quality(_domain(url)),3),
                    "evidence_score":round(score,3),"marketing_claim_flag":mflag,"claimed_performance":claim})
    return out

class LearningDB:
    def __init__(self,path):
        self.path=path
        self.conn=sqlite3.connect(path,check_same_thread=False)
        self.conn.row_factory=sqlite3.Row
        self.conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources(domain TEXT PRIMARY KEY,first_seen REAL,last_seen REAL,language TEXT,homepage TEXT,pages_scanned INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS pages(url TEXT PRIMARY KEY,domain TEXT NOT NULL,fetched_at REAL,title TEXT,fingerprint TEXT,simhash TEXT,content_chars INTEGER DEFAULT 0,http_status INTEGER,language TEXT);
        CREATE TABLE IF NOT EXISTS method_evidence(id INTEGER PRIMARY KEY AUTOINCREMENT,method_id TEXT NOT NULL,url TEXT NOT NULL,domain TEXT NOT NULL,
          language TEXT,evidence_score REAL NOT NULL,evidence_json TEXT NOT NULL,marketing_claim_flag INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,
          UNIQUE(method_id,url));
        CREATE TABLE IF NOT EXISTS demo_results(id INTEGER PRIMARY KEY AUTOINCREMENT,method_id TEXT NOT NULL,result TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS candle_forecasts(
          pair TEXT NOT NULL,prediction_ts INTEGER NOT NULL,target_ts INTEGER NOT NULL,direction TEXT NOT NULL,probability REAL NOT NULL,
          feature_json TEXT NOT NULL,result TEXT,created_at REAL NOT NULL,PRIMARY KEY(pair,prediction_ts));
        CREATE TABLE IF NOT EXISTS model_weights(name TEXT PRIMARY KEY,weight REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS strategy_candidates(
          method_id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          practice_started_at REAL,
          practice_until REAL,
          human_accepted_at REAL,
          last_demo_at REAL,
          last_error_learning_at REAL,
          updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS research_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,started_at REAL,finished_at REAL,discovered INTEGER,fetched INTEGER,evidence INTEGER,errors INTEGER);
        CREATE INDEX IF NOT EXISTS idx_evidence_method ON method_evidence(method_id);
        CREATE INDEX IF NOT EXISTS idx_forecast_pair ON candle_forecasts(pair);
        """)
        self.conn.commit()
    def meta(self,k,d=""):
        r=self.conn.execute("SELECT value FROM meta WHERE key=?",(k,)).fetchone();return str(r["value"]) if r else d
    def set_meta(self,k,v):
        self.conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,str(v)));self.conn.commit()
    def add_source(self,url,language):
        d=_domain(url);now=time.time()
        row=self.conn.execute("SELECT domain FROM sources WHERE domain=?",(d,)).fetchone()
        if row:
            self.conn.execute("UPDATE sources SET last_seen=?,language=COALESCE(language,?),homepage=COALESCE(homepage,?) WHERE domain=?",(now,language,url,d))
            self.conn.commit();return False
        self.conn.execute("INSERT INTO sources(domain,first_seen,last_seen,language,homepage) VALUES(?,?,?,?,?)",(d,now,now,language,url));self.conn.commit();return True
    def source_count(self):return int(self.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0])
    def pages_count(self):return int(self.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0])
    def page_seen(self,u):return self.conn.execute("SELECT 1 FROM pages WHERE url=?",(u,)).fetchone() is not None
    def near_duplicate(self,sh,limit=5):
        rows=self.conn.execute("SELECT simhash FROM pages WHERE simhash IS NOT NULL ORDER BY fetched_at DESC LIMIT 5000").fetchall()
        return any(_hamming(sh,r["simhash"])<=limit for r in rows)
    def save_page(self,url,title,fp,sh,chars,status,language):
        d=_domain(url);now=time.time()
        self.conn.execute("""INSERT OR REPLACE INTO pages(url,domain,fetched_at,title,fingerprint,simhash,content_chars,http_status,language)
          VALUES(?,?,?,?,?,?,?,?,?)""",(url,d,now,title,fp,sh,chars,status,language))
        self.conn.execute("UPDATE sources SET pages_scanned=pages_scanned+1,last_seen=? WHERE domain=?",(now,d));self.conn.commit()
    def save_evidence(self,item):
        self.conn.execute("""INSERT OR IGNORE INTO method_evidence
          (method_id,url,domain,language,evidence_score,evidence_json,marketing_claim_flag,created_at)
          VALUES(?,?,?,?,?,?,?,?)""",(item["method_id"],item["url"],item["domain"],item["language"],item["evidence_score"],
          json.dumps(item,ensure_ascii=False),int(item["marketing_claim_flag"]),time.time()));self.conn.commit()
    def method_rows(self):
        return self.conn.execute("""SELECT method_id,COUNT(DISTINCT domain) domains,AVG(evidence_score) avg_score,
          SUM(CASE WHEN marketing_claim_flag=0 THEN 1 ELSE 0 END) clean_evidence FROM method_evidence GROUP BY method_id
          ORDER BY domains DESC,avg_score DESC""").fetchall()
    def add_demo(self,method_id,result):
        result=str(result).upper()
        if result in {"WIN","LOSS","TIE"}:
            self.conn.execute("INSERT INTO demo_results(method_id,result,created_at) VALUES(?,?,?)",(method_id,result,time.time()));self.conn.commit()
    def demo_stats(self,method_id):
        rows=self.conn.execute("SELECT result,COUNT(*) n FROM demo_results WHERE method_id=? GROUP BY result",(method_id,)).fetchall()
        d={r["result"]:int(r["n"]) for r in rows};total=sum(d.values())
        return {"samples":total,"wins":d.get("WIN",0),"losses":d.get("LOSS",0),"ties":d.get("TIE",0),"win_rate":round(100*d.get("WIN",0)/max(1,total),2)}

    def ensure_candidate(self,method_id):
        mid=str(method_id)
        now=time.time()
        row=self.conn.execute("SELECT method_id,status FROM strategy_candidates WHERE method_id=?",(mid,)).fetchone()
        if row:return str(row["status"])
        self.conn.execute(
            "INSERT INTO strategy_candidates(method_id,status,updated_at) VALUES(?,?,?)",
            (mid,"DISCOVERED",now),
        )
        self.conn.commit()
        return "DISCOVERED"

    def candidate(self,method_id):
        return self.conn.execute(
            "SELECT * FROM strategy_candidates WHERE method_id=?",(str(method_id),)
        ).fetchone()

    def start_practice(self,method_id,started_at=None):
        now=time.time() if started_at is None else float(started_at)
        row=self.candidate(method_id)
        if row and str(row["status"]) in {"VALIDATED","REJECTED"}:
            raise ValueError("Candidate is not eligible for a new practice cycle")
        until=now+2*60*60
        self.conn.execute(
            """INSERT INTO strategy_candidates(method_id,status,practice_started_at,practice_until,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(method_id) DO UPDATE SET
                 status='PRACTICE',practice_started_at=excluded.practice_started_at,
                 practice_until=excluded.practice_until,updated_at=excluded.updated_at""",
            (str(method_id),"PRACTICE",now,until,now),
        )
        self.conn.commit()
        return True

    def refresh_candidate_state(self,method_id,now=None):
        now=time.time() if now is None else float(now)
        row=self.candidate(method_id)
        if not row:return None
        status=str(row["status"])
        until=float(row["practice_until"] or 0)
        if status=="PRACTICE" and until and now>=until:
            self.conn.execute(
                "UPDATE strategy_candidates SET status='AWAITING_HUMAN_ACCEPT',updated_at=? WHERE method_id=?",
                (now,str(method_id)),
            )
            self.conn.commit()
            row=self.candidate(method_id)
        return row

    def human_accept(self,method_id,accepted_at=None):
        now=time.time() if accepted_at is None else float(accepted_at)
        row=self.refresh_candidate_state(method_id,now)
        if not row or str(row["status"])!="AWAITING_HUMAN_ACCEPT":
            raise ValueError("Human ACCEPT requires completed 2-hour demo practice")
        self.conn.execute(
            "UPDATE strategy_candidates SET status='HUMAN_ACCEPTED',human_accepted_at=?,updated_at=? WHERE method_id=?",
            (now,now,str(method_id)),
        )
        self.conn.commit()
        return True

    def mark_error_learning(self,method_id,learned_at=None):
        now=time.time() if learned_at is None else float(learned_at)
        row=self.candidate(method_id)
        if not row or str(row["status"])!="HUMAN_ACCEPTED":
            raise ValueError("Error learning requires Human ACCEPT")
        self.conn.execute(
            "UPDATE strategy_candidates SET last_error_learning_at=?,updated_at=? WHERE method_id=?",
            (now,now,str(method_id)),
        )
        self.conn.commit()
        return True

    def forecast_stats(self):
        r=self.conn.execute("""SELECT COUNT(*) n,SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
          SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) losses FROM candle_forecasts WHERE result IS NOT NULL""").fetchone()
        n=int(r["n"] or 0);w=int(r["wins"] or 0);l=int(r["losses"] or 0)
        return {"samples":n,"wins":w,"losses":l,"accuracy":round(100*w/max(1,w+l),2)}
    def pair_forecast_stats(self,pair):
        r=self.conn.execute("""SELECT COUNT(*) n,SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins
          FROM candle_forecasts WHERE pair=? AND result IS NOT NULL""",(pair,)).fetchone()
        n=int(r["n"] or 0);w=int(r["wins"] or 0);return {"samples":n,"wins":w,"accuracy":round(100*w/max(1,n),2)}
    def load_weights(self):
        rows=self.conn.execute("SELECT name,weight FROM model_weights").fetchall()
        return {r["name"]:float(r["weight"]) for r in rows}
    def save_weights(self,w):
        self.conn.executemany("INSERT INTO model_weights(name,weight) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET weight=excluded.weight",
                              list(w.items()));self.conn.commit()

class ForwardCandleModel:
    """Online logistic model restricted to AVWAP + Volume Profile features."""
    NAMES=("bias","avwap_side","poc_side","vah_side","val_side","avwap_slope","profile_position")

    def __init__(self,db):
        self.db=db
        self.w={n:0.0 for n in self.NAMES}
        self.w.update(db.load_weights())

    @staticmethod
    def _indicators(cs):
        from candice_brain import _aggregate_closed_15m, _anchored_vwap, _volume_profile
        blocks=_aggregate_closed_15m(cs,time.time())
        if not blocks:
            return None
        anchor=int(blocks[-1]["time"])
        avwap,_=_anchored_vwap(cs,anchor)
        prev_avwap,_=_anchored_vwap(cs[:-1],anchor)
        vp=_volume_profile(cs)
        if not vp or avwap<=0:
            return None
        px=float(cs[-1]["close"])
        span=max(vp["range_high"]-vp["range_low"],1e-12)
        return {
            "avwap":avwap,
            "poc":vp["poc"],
            "vah":vp["vah"],
            "val":vp["val"],
            "slope":avwap-prev_avwap,
            "position":(px-vp["range_low"])/span,
        }

    @staticmethod
    def features(cs):
        if len(cs)<60:
            return None
        ind=ForwardCandleModel._indicators(cs[:-1] if len(cs)>60 else cs)
        if not ind:
            return None
        px=float(cs[-1]["close"])
        return {
            "bias":1.0,
            "avwap_side":1.0 if px>ind["avwap"] else -1.0,
            "poc_side":1.0 if px>ind["poc"] else -1.0,
            "vah_side":1.0 if px>ind["vah"] else -1.0,
            "val_side":1.0 if px<ind["val"] else -1.0,
            "avwap_slope":ind["slope"]/max(abs(px),1e-12)*100.0,
            "profile_position":ind["position"]*2.0-1.0,
        }

    def predict(self,features):
        z=sum(self.w.get(k,0)*v for k,v in features.items())
        z=max(-8,min(8,z))
        p=1/(1+math.exp(-z))
        return ("UP" if p>=.5 else "DOWN",p)

    def update(self,features,label,lr=.04):
        y=1.0 if label=="UP" else 0.0
        d=y-self.predict(features)[1]
        for k,v in features.items():
            self.w[k]=self.w.get(k,0)+lr*d*v
        self.db.save_weights(self.w)

class SelfLearningEngine:
    def __init__(self):
        self.db=LearningDB(DB_PATH);stored=self.db.meta("started_at","").strip()
        if stored:self.started_at=float(stored)
        else:
            raw=os.getenv("LEARNING_START_UTC",DEFAULT_LEARNING_START_UTC).strip()
            try:self.started_at=datetime.fromisoformat(raw.replace("Z","+00:00")).astimezone(timezone.utc).timestamp()
            except Exception:self.started_at=time.time()
            self.db.set_meta("started_at",self.started_at)
        self.model=ForwardCandleModel(self.db)
        self.robot_cache={}
        self.http_gate=asyncio.Semaphore(MAX_CONCURRENCY)
        self.search_gate=asyncio.Semaphore(SEARCH_CONCURRENCY)
        self.request_rate_lock=asyncio.Lock()
        self.next_request_at=0.0
        self.queue=asyncio.Queue()
        self.enqueued=set()
        self.last_market_ts={}
        self.pending_forecasts={}
        self.metrics={"discovered":0,"fetched":0,"errors":0,"evidence":0,"runs":0,"searches":0,"deduped":0}
        self.lang_counts=defaultdict(int)

    def day(self):
        return min(LEARNING_DAYS,max(1,int(max(0,time.time()-self.started_at)//86400)+1))
    def stage(self):
        stages=["AVWAP foundations","AVWAP anchoring methods","AVWAP slope and acceptance","AVWAP false-signal research",
                "Volume Profile foundations","POC/VAH/VAL behaviour","HVN/LVN research","Volume Profile false signals",
                "AVWAP + Volume Profile confluence","M1 regime and session effects","OTC/data-quality effects",
                "costs/slippage/latency sensitivity","walk-forward and out-of-sample tests","demo validation","two-indicator final audit"]
        return stages[self.day()-1]
    def status(self):
        domains=self.db.source_count(); remaining=max(0,TARGET_SITES-domains)
        hours_left=max(1,(LEARNING_DAYS-(self.day()-1))*24)
        return {"enabled":os.getenv("SELF_LEARNING_ENABLED","true").strip().lower()!="false",
                "program_day":self.day(),"program_total_days":LEARNING_DAYS,"stage":self.stage(),
                "target_sites":TARGET_SITES,"unique_sites":domains,"sites_remaining":remaining,
                "progress_pct":round(100*domains/TARGET_SITES,2),"daily_target":TARGET_DAILY,
                "required_sites_per_hour":round(remaining/hours_left,1),"pages_scanned":self.db.pages_count(),
                "queue_depth":self.queue.qsize(),"languages_seen":dict(sorted(self.lang_counts.items(),key=lambda x:-x[1])[:24]),
                "strategy_candidates":int(self.db.conn.execute("SELECT COUNT(*) FROM strategy_candidates").fetchone()[0]),
                "next_candle_model":self.db.forecast_stats(),"metrics":dict(self.metrics)}
    def _enqueue(self,url,language,origin,priority=0):
        u=_clean_url(url)
        if not u or self.db.page_seen(u):return
        key=u
        if key in self.enqueued:return
        self.enqueued.add(key)
        try:self.queue.put_nowait((u,language,origin,priority));self.metrics["discovered"]+=1
        except asyncio.QueueFull:pass
    async def _pace_request(self):
        """Keep research traffic sparse so it cannot compete with the live cycle."""
        async with self.request_rate_lock:
            now=time.monotonic()
            wait=max(0.0,self.next_request_at-now)
            self.next_request_at=max(now,self.next_request_at)+REQUEST_GAP_SECONDS
        if wait:
            await asyncio.sleep(wait)

    async def _robots_ok(self,url):
        d=_domain(url);now=time.time();cached=self.robot_cache.get(d)
        if cached and now-cached[0]<21600:
            rp=cached[1];return True if rp is None else rp.can_fetch(USER_AGENT,url)
        rp=RobotFileParser()
        try:
            async with self.http_gate:
                await self._pace_request()
                async with httpx.AsyncClient(timeout=httpx.Timeout(5,connect=3),headers={"User-Agent":USER_AGENT},follow_redirects=True) as c:
                    r=await c.get(f"https://{d}/robots.txt")
                if r.status_code>=400:rp=None
                else:rp.parse(r.text.splitlines())
        except Exception:rp=None
        self.robot_cache[d]=(now,rp)
        return True if rp is None else rp.can_fetch(USER_AGENT,url)
    async def _fetch(self,url):
        async with self.http_gate:
            await self._pace_request()
            async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT,connect=4),follow_redirects=True,
                                         headers={"User-Agent":USER_AGENT,"Accept":"text/html,application/xhtml+xml"}) as c:
                r=await c.get(url);r.raise_for_status()
            ctype=r.headers.get("content-type","").lower()
            if "html" not in ctype:return "","",r.status_code,[]
            parser=PageParser();parser.feed(r.content[:MAX_PAGE_BYTES].decode(r.encoding or "utf-8","ignore"))
            return _normalize_text(" ".join(parser.text_parts))," ".join(parser.title_parts).strip(),r.status_code,parser.links
    async def _crawl_one(self,item):
        url,lang,origin,_=item
        if not await self._robots_ok(url):return
        try:
            body,title,status,links=await self._fetch(url)
            if len(body)<120:return
            detected=_script_language(body) if lang in {"unknown","auto"} else lang
            fp=_fingerprint(body);sh=_simhash(body)
            self.db.add_source(url,detected)
            if self.db.near_duplicate(sh) and self.db.pages_count()>100:
                self.metrics["deduped"]+=1
            else:
                self.db.save_page(url,title,fp,sh,len(body),status,detected)
                self.metrics["fetched"]+=1
                self.lang_counts[detected]+=1
                for e in _extract_evidence(body,title,url,detected):
                    self.db.save_evidence(e);self.metrics["evidence"]+=1
            # Sitemap and focused same-domain expansion.
            d=_domain(url);path=urlparse(url).path.lower()
            if path in {"/",""} or path.endswith("home"):
                for sm in (f"https://{d}/sitemap.xml",f"https://{d}/sitemap_index.xml"):
                    self._enqueue(sm,detected,"sitemap")
            added=0
            for href,label in links:
                if added>=5:break
                target=_clean_url(_unwrap_search_href(href),url)
                if not target or _domain(target)!=d:continue
                t=(target+" "+label).lower()
                if any(x in t for x in ("/login","/signup","/register","/account","/checkout")):continue
                if any(k in t for k in ("m1","1-minute","1minute","vwap","anchored-vwap","avwap","volume-profile",
                                        "poc","point-of-control","vah","val","value-area","hvn","lvn",
                                        "volume-at-price","next-candle","forecast")):
                    self._enqueue(target,detected,"internal");added+=1
        except Exception as e:
            self.metrics["errors"]+=1;log.debug("LEARNING_FETCH_FAILED url=%s error=%s",url,e)
    async def _search(self,query,language):
        self.metrics["searches"]+=1
        async with self.search_gate:
            endpoints=[
                "https://html.duckduckgo.com/html/?q="+quote_plus(query),
                "https://www.google.com/search?q="+quote_plus(query)+"&num=20",
                "https://www.bing.com/search?q="+quote_plus(query),
            ]
            for endpoint in endpoints:
                try:
                    await self._pace_request()
                    async with httpx.AsyncClient(timeout=httpx.Timeout(8,connect=4),headers={"User-Agent":USER_AGENT},follow_redirects=True) as client:
                        r=await client.get(endpoint)
                    if r.status_code>=400:
                        continue
                    p=PageParser()
                    p.feed(r.text)
                    found=0
                    for href,label in p.links:
                        target=_clean_url(_unwrap_search_href(href))
                        if not target:
                            continue
                        d=_domain(target)
                        if not d or d in SEARCH_HOSTS:
                            continue
                        self._enqueue(target,language,"search")
                        found+=1
                    if found:
                        return found
                except Exception as e:
                    self.metrics["errors"]+=1
                    log.debug("LEARNING_SEARCH_FAILED query=%s %s",query,e)
            return 0

    def _queries(self):
        methods=list(METHOD_LIBRARY)
        # Query families intentionally emphasize pre-candle / forward-only research.
        suffixes=["backtest","out of sample","walk forward","next candle","M1","1 minute"]
        allq=[]
        for lang,base in LANGUAGE_QUERIES.items():
            for q in base:
                allq.append((q,lang))
            for m in methods:
                label=m.replace("_"," ")
                allq.append((f"{q if base else ''} {label} next candle backtest",lang))
        # Deterministic rotation avoids repeating the same first pages forever.
        return allq
    async def _discover(self):
        qs=self._queries();n=len(qs)
        start=(self.metrics["runs"]*11+self.day()*7)%max(1,n)
        chosen=[]
        # 12 queries/run across languages; adaptive when far behind target.
        batch=12 if self.db.source_count()<TARGET_SITES else 4
        for i in range(min(batch,n)):
            item=qs[(start+i)%n]
            if item not in chosen:chosen.append(item)
        found=await asyncio.gather(*(self._search(q,l) for q,l in chosen),return_exceptions=True)
        return sum(int(x) for x in found if isinstance(x,int))
    def _market_normalize(self,candles):
        out=[]
        for c in candles or []:
            if not isinstance(c,dict):continue
            try:
                t=float(c.get("time",c.get("t")));o=float(c.get("open",c.get("o")));h=float(c.get("high",c.get("h")))
                lo=float(c.get("low",c.get("l")));cl=float(c.get("close",c.get("c")))
                if t>20_000_000_000:t/=1000
                v=float(c.get("volume",c.get("v",1.0)) or 1.0)
                out.append({"time":int(t//60)*60,"open":o,"high":h,"low":lo,"close":cl,"volume":v})
            except Exception:continue
        uniq={x["time"]:x for x in out};return [uniq[k] for k in sorted(uniq)]
    def record_market_snapshot(self,pair,candles,price=None,timestamp=None):
        cs=self._market_normalize(candles)
        if len(cs)<25:return
        now=time.time() if timestamp is None else float(timestamp)
        # Learning labels are derived only from fully closed M1 candles.
        closed=[x for x in cs if int(x["time"])+60 <= now-1]
        if len(closed)<25:return
        latest=closed[-1];ts=int(latest["time"])
        if self.last_market_ts.get(pair)==ts:return
        self.last_market_ts[pair]=ts
        # First close of a new candle settles the prediction made before it existed.
        pending=self.pending_forecasts.get(pair)
        if pending and int(pending["target_ts"])==ts:
            actual="UP" if latest["close"]>pending["anchor_close"] else "DOWN" if latest["close"]<pending["anchor_close"] else "TIE"
            result="WIN" if actual==pending["direction"] else "LOSS" if actual in {"UP","DOWN"} else "TIE"
            self.db.conn.execute("UPDATE candle_forecasts SET result=? WHERE pair=? AND prediction_ts=?",
                                 (result,pair,pending["prediction_ts"]));self.db.conn.commit()
            if result=="WIN":self.model.update(pending["features"],actual)
            elif result=="LOSS":self.model.update(pending["features"],actual)
            self.pending_forecasts.pop(pair,None)
        anchor=cs[:-0] if False else cs
        f=self.model.features(anchor)
        if not f:return
        direction,p=self.model.predict(f)
        pred_ts=int(latest["time"]);target_ts=pred_ts+60
        try:
            self.db.conn.execute("""INSERT OR IGNORE INTO candle_forecasts
              (pair,prediction_ts,target_ts,direction,probability,feature_json,created_at)
              VALUES(?,?,?,?,?,?,?)""",(pair,pred_ts,target_ts,direction,float(p),json.dumps(f),now))
            self.db.conn.commit()
            self.pending_forecasts[pair]={"prediction_ts":pred_ts,"target_ts":target_ts,"direction":direction,
                                          "probability":p,"features":f,"anchor_close":float(latest["close"])}
        except Exception as e:log.debug("FORECAST_STORE_FAILED pair=%s %s",pair,e)
    def synthesize(self):
        methods=[]
        for row in self.db.method_rows():
            if str(row["method_id"]) not in METHOD_LIBRARY:
                continue
            stats=self.db.demo_stats(row["method_id"])
            web_ok=int(row["domains"])>=5 and float(row["avg_score"])>=.55 and int(row["clean_evidence"])>=5
            mid=str(row["method_id"])
            self.db.ensure_candidate(mid)
            candidate=self.db.refresh_candidate_state(mid)
            status=str(candidate["status"]) if candidate else ("DISCOVERED" if web_ok else "WATCH")
            methods.append({
                "method_id":mid,
                "independent_domains":int(row["domains"]),
                "avg_evidence_score":round(float(row["avg_score"]),3),
                "clean_evidence":int(row["clean_evidence"]),
                "demo":stats,
                "status":status,
                "active_live_brain":False,
                "validated_version":0,
            })
        fs=self.db.forecast_stats()
        own="WATCH" if fs["samples"]<100 else ("FORWARD_VALIDATED_CANDIDATE" if fs["accuracy"]>=55 else "REJECT_AND_RESEARCH")
        result={"program":{"days":LEARNING_DAYS,"target_sites":TARGET_SITES,"started_at":self.started_at,
                           "day":self.day(),"stage":self.stage()},
                "rules":{"web_claims_are_not_proof":True,"forward_only_labels":True,"no_future_leakage":True,
                         "minimum_independent_domains":5,"forecast_samples_for_candidate":100,"auto_trade":False,
                         "live_brain_locked_to_two_indicators":True},
                "next_candle_model":{"status":own,"stats":fs,"features":list(ForwardCandleModel.NAMES)},
                "methods":methods,"status":self.status()}
        tmp=STATE_JSON+".tmp"
        with open(tmp,"w",encoding="utf-8") as f:json.dump(result,f,ensure_ascii=False,indent=2)
        os.replace(tmp,STATE_JSON)
        return result

    async def run_once(self):
        self.metrics["runs"]+=1;started=time.time()
        found=await self._discover()
        workers=[]
        while not self.queue.empty() and len(workers)<MAX_CONCURRENCY*2:
            workers.append(asyncio.create_task(self._crawl_one(await self.queue.get())))
        if workers:await asyncio.gather(*workers,return_exceptions=True)
        snap=self.synthesize()
        self.db.conn.execute("INSERT INTO research_runs(started_at,finished_at,discovered,fetched,evidence,errors) VALUES(?,?,?,?,?,?)",
                             (started,time.time(),found,self.metrics["fetched"],self.metrics["evidence"],self.metrics["errors"]));self.db.conn.commit()
        log.info("SELF_LEARNING_PROGRESS day=%d stage=%s sites=%d/%d pages=%d evidence=%d queue=%d next1m=%s",
                 self.day(),self.stage(),self.db.source_count(),TARGET_SITES,self.db.pages_count(),
                 len(snap["methods"]),self.queue.qsize(),snap["next_candle_model"])
        return snap
    async def loop(self):
        if os.getenv("SELF_LEARNING_ENABLED","true").strip().lower()=="false":
            log.info("SELF_LEARNING_DISABLED");return
        while True:
            try:await self.run_once()
            except Exception:log.exception("SELF_LEARNING_RUN_FAILED")
            await asyncio.sleep(RUN_INTERVAL_SECONDS)
    def record_demo_result(self,method_id,result):
        mid=str(method_id)
        if mid not in METHOD_LIBRARY:
            return
        self.db.add_demo(mid,result)
        row=self.db.candidate(mid)
        now=time.time()
        if row:
            self.db.conn.execute(
                "UPDATE strategy_candidates SET last_demo_at=?,updated_at=? WHERE method_id=?",
                (now,now,mid),
            )
            self.db.conn.commit()

LEARNING_ENGINE=SelfLearningEngine()
def learning_status():return LEARNING_ENGINE.status()
def record_demo_result(method_id,result):LEARNING_ENGINE.record_demo_result(method_id,result)
def start_demo_practice(method_id):return LEARNING_ENGINE.db.start_practice(method_id)
def human_accept_candidate(method_id):return LEARNING_ENGINE.db.human_accept(method_id)
def mark_error_learning(method_id):return LEARNING_ENGINE.db.mark_error_learning(method_id)
def record_market_snapshot(pair,candles,price=None,timestamp=None):LEARNING_ENGINE.record_market_snapshot(pair,candles,price,timestamp)
async def self_learning_loop():await LEARNING_ENGINE.loop()
