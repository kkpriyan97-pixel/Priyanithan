"""15-day multilingual trading research + own-strategy learning layer.

This module is deliberately separated from the live Candice technical brain.
It discovers and reads public educational/research pages, extracts M1-related
methods/evidence, de-duplicates sources, stores them in SQLite, and produces
a candidate own-strategy library.

Web claims are treated as evidence, never as proof of profitability. Only
demo-validated methods can become eligible for future strategy promotion.
No order execution, login, or broker automation is performed here.
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
from collections import defaultdict
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger("candice.learning")

LEARNING_DAYS = 15
TARGET_SITES = 10_000
TARGET_DAILY = math.ceil(TARGET_SITES / LEARNING_DAYS)
MAX_CONCURRENCY = int(os.getenv("LEARNING_MAX_CONCURRENCY", "6"))
REQUEST_TIMEOUT = float(os.getenv("LEARNING_REQUEST_TIMEOUT", "10"))
RUN_INTERVAL_SECONDS = int(os.getenv("LEARNING_RUN_INTERVAL", "900"))
MAX_PAGE_BYTES = int(os.getenv("LEARNING_MAX_PAGE_BYTES", "700000"))
DB_PATH = os.getenv("LEARNING_DB_PATH", "candice_learning.sqlite3")
STATE_JSON = os.getenv("LEARNING_STATE_JSON", "candice_learning_state.json")
USER_AGENT = "CANDICE-M1-ResearchBot/1.0"

LANGUAGE_QUERIES = {
    "en": [
        "1 minute scalping strategy price action EMA RSI VWAP breakout retest",
        "M1 trading strategy candlestick market structure momentum",
        "one minute trading setup higher timeframe confirmation",
        "1 minute scalping false signals volatility session filter",
    ],
    "es": [
        "estrategia scalping 1 minuto acción del precio EMA RSI VWAP",
        "estrategia trading M1 velas estructura de mercado",
        "scalping un minuto confirmación temporal superior",
    ],
    "pt": [
        "estratégia scalping 1 minuto ação do preço EMA RSI VWAP",
        "estratégia M1 velas estrutura de mercado momentum",
        "scalping um minuto confirmação timeframe superior",
    ],
    "fr": [
        "stratégie scalping 1 minute price action EMA RSI VWAP",
        "trading M1 chandeliers structure de marché",
        "scalping une minute confirmation unité de temps supérieure",
    ],
    "de": [
        "1 Minute Scalping Strategie Price Action EMA RSI VWAP",
        "M1 Trading Strategie Kerzen Marktstruktur Momentum",
        "Skalping 1 Minute höherer Zeitrahmen Bestätigung",
    ],
    "ru": [
        "стратегия скальпинга 1 минута price action EMA RSI VWAP",
        "торговля M1 свечи структура рынка импульс",
        "скальпинг 1 минута подтверждение старшего таймфрейма",
    ],
    "zh": [
        "1分钟 剥头皮 交易策略 价格行为 EMA RSI VWAP",
        "M1 交易 K线 市场结构 动量 策略",
        "1分钟 交易 高时间框架 确认",
    ],
    "ja": [
        "1分足 スキャルピング 手法 プライスアクション EMA RSI VWAP",
        "M1 トレード ローソク足 市場構造 モメンタム",
        "1分足 上位足 確認 スキャルピング",
    ],
    "ko": [
        "1분봉 스캘핑 전략 가격행동 EMA RSI VWAP",
        "M1 매매 캔들 시장구조 모멘텀",
        "1분 스캘핑 상위 시간봉 확인",
    ],
    "ar": [
        "استراتيجية سكالبينج دقيقة واحدة حركة السعر EMA RSI VWAP",
        "تداول M1 شموع هيكل السوق زخم",
        "سكالبينج دقيقة واحدة تأكيد الإطار الزمني الأعلى",
    ],
    "hi": [
        "1 मिनट स्कैल्पिंग रणनीति प्राइस एक्शन EMA RSI VWAP",
        "M1 ट्रेडिंग कैंडल मार्केट स्ट्रक्चर मोमेंटम",
        "1 मिनट स्कैल्पिंग उच्च टाइमफ्रेम पुष्टि",
    ],
    "tr": [
        "1 dakikalık scalping stratejisi fiyat hareketi EMA RSI VWAP",
        "M1 işlem mum formasyonu piyasa yapısı momentum",
        "1 dakika scalping üst zaman dilimi onayı",
    ],
    "id": [
        "strategi scalping 1 menit price action EMA RSI VWAP",
        "trading M1 candlestick struktur pasar momentum",
        "scalping satu menit konfirmasi timeframe lebih tinggi",
    ],
    "vi": [
        "chiến lược scalping 1 phút price action EMA RSI VWAP",
        "giao dịch M1 nến cấu trúc thị trường động lượng",
        "scalping 1 phút xác nhận khung thời gian lớn",
    ],
    "th": [
        "กลยุทธ์ scalping 1 นาที price action EMA RSI VWAP",
        "เทรด M1 แท่งเทียน โครงสร้างตลาด โมเมนตัม",
        "scalping 1 นาที ยืนยันกรอบเวลาที่สูงกว่า",
    ],
    "it": [
        "strategia scalping 1 minuto price action EMA RSI VWAP",
        "trading M1 candele struttura di mercato momentum",
        "scalping 1 minuto conferma timeframe superiore",
    ],
}

METHOD_TERMS = {
    "trend_following": [
        "trend following", "trend-following", "ema alignment", "moving average alignment",
        "tendance", "tendencia", "trendfolge", "трендовая торговля", "趋势交易", "トレンドフォロー",
    ],
    "pullback_retest": [
        "pullback", "retest", "pull-back", "retest breakout", "reteste", "pullback entry",
        "ретест", "откат", "回踩", "押し目", "되돌림", "retoma",
    ],
    "breakout": [
        "breakout", "break-out", "range breakout", "opening range", "cassure", "ruptura",
        "ausbruch", "пробой", "突破", "ブレイクアウト", "돌파",
    ],
    "rejection_reversal": [
        "rejection", "reversal", "pin bar", "engulfing", "mean reversion", "reversal candle",
        "rechazo", "reversão", "rejet", "umkehr", "разворот", "反转", "反発",
    ],
    "vwap_momentum": [
        "vwap", "volume weighted average price", "momentum", "volume spike", "activity spike",
        "volume weighted", "волатильность", "成交量", "モメンタム",
    ],
    "rsi_divergence": [
        "rsi divergence", "divergence rsi", "divergencia rsi", "divergence RSI",
        "дивергенция rsi", "rsi 背离", "rsi ダイバージェンス",
    ],
    "bollinger_mean_reversion": [
        "bollinger", "bollinger bands", "band touch", "band reversal",
        "bandes de bollinger", "bandas de bollinger", "боллинджер", "布林带",
    ],
    "market_structure": [
        "market structure", "higher high", "lower low", "hh hl", "lh ll", "support resistance",
        "structure de marché", "estructura de mercado", "marktstruktur", "структура рынка",
        "市场结构", "市場構造",
    ],
}

QUALITY_HINTS = {
    "edu": 1.0,
    "gov": 1.0,
    "org": 0.8,
    "edu.au": 1.0,
    "ac.uk": 1.0,
    "tradingview.com": 0.65,
    "cmegroup.com": 0.9,
    "investor.gov": 1.0,
    "sec.gov": 1.0,
    "ig.com": 0.7,
    "oanda.com": 0.7,
    "babypips.com": 0.55,
}

MARKETING_PATTERNS = re.compile(
    r"(90\\s*%|95\\s*%|99\\s*%|guaranteed|guarantee|guaranteed profit|"
    r"profit every day|sure win|no loss|稳赚|稳赚不赔|100\\s*% win|"
    r"ganancia garantizada|lucro garantido|profit garanti|гарантированн)",
    re.I,
)


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._title = False
        self._skip = 0
        self._anchor: str | None = None
        self._anchor_text: list[str] = []
        self._capture = {"p", "h1", "h2", "h3", "li", "article", "section", "title"}

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip += 1
            return
        if tag == "title":
            self._title = True
        if tag == "a" and self._skip == 0:
            href = attrs.get("href")
            if href:
                self._anchor = href
                self._anchor_text = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title":
            self._title = False
        if tag == "a" and self._anchor:
            label = " ".join(self._anchor_text).strip()
            self.links.append((self._anchor, label))
            self._anchor = None
            self._anchor_text = []

    def handle_data(self, data):
        if self._skip:
            return
        s = " ".join(data.split())
        if not s:
            return
        if self._title:
            self.title_parts.append(s)
        if self._anchor is not None:
            self._anchor_text.append(s)
        if any(piece in s.lower() for piece in (
            "1 minute", "1-minute", "m1", "scalp", "scalping", "price action",
            "ema", "rsi", "vwap", "bollinger", "candlestick", "breakout",
            "pullback", "retest", "market structure", "support", "resistance",
        )):
            self.text_parts.append(s)


def _clean_url(raw: str, base: str | None = None) -> str | None:
    if base:
        raw = urljoin(base, raw)
    raw = html.unescape(raw.strip())
    if raw.startswith("//"):
        raw = "https:" + raw
    p = urlparse(raw)
    if p.scheme not in {"http", "https"} or not p.netloc:
        return None
    bad = {"javascript:", "mailto:", "tel:"}
    if p.scheme + ":" in bad:
        return None
    host = p.hostname.lower() if p.hostname else ""
    if not host or host == "localhost" or host.endswith(".local"):
        return None
    path = p.path or "/"
    # Remove tracking parameters; retain normal query parameters.
    q = {k: v for k, v in parse_qs(p.query).items() if not k.lower().startswith(("utm_", "fbclid"))}
    query = "&".join(f"{k}={quote_plus(v[0])}" for k, v in sorted(q.items()))
    return urlunparse((p.scheme, host, path, "", query, ""))


def _domain(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.lower().removeprefix("www.")


def _source_quality(domain: str) -> float:
    d = domain.lower()
    if d in QUALITY_HINTS:
        return QUALITY_HINTS[d]
    if d.endswith(".gov") or d.endswith(".edu"):
        return 1.0
    if d.endswith(".org"):
        return 0.75
    return 0.45


def _content_fingerprint(text: str) -> str:
    normalized = re.sub(r"\\s+", " ", text.lower()).strip()
    return hashlib.sha256(normalized[:12000].encode("utf-8", "ignore")).hexdigest()


def _extract_candidates(text: str, title: str, url: str) -> list[dict]:
    lower = text.lower()
    out = []
    for method_id, terms in METHOD_TERMS.items():
        hits = [t for t in terms if t in lower]
        if not hits:
            continue
        sentences = re.split(r"(?<=[.!?。！？])\\s+", text)
        matched = []
        for s in sentences:
            ls = s.lower()
            if any(t in ls for t in hits):
                matched.append(s.strip())
        matched = [s for s in matched if 25 <= len(s) <= 900][:6]
        specificity = min(1.0, (len(hits) / 3.0) + (0.2 if "1-minute" in lower or "1 minute" in lower or "m1" in lower else 0))
        promo_penalty = 0.35 if MARKETING_PATTERNS.search(text) else 0.0
        evidence_score = max(0.0, min(1.0, _source_quality(_domain(url)) * 0.7 + specificity * 0.3 - promo_penalty))
        if matched:
            out.append({
                "method_id": method_id,
                "title": title[:240],
                "url": url,
                "domain": _domain(url),
                "matched_terms": hits[:12],
                "evidence": matched,
                "source_quality": round(_source_quality(_domain(url)), 3),
                "evidence_score": round(evidence_score, 3),
                "marketing_claim_flag": bool(promo_penalty),
            })
    return out


class LearningDB:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(
                domain TEXT PRIMARY KEY,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                language TEXT,
                homepage TEXT,
                pages_scanned INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pages(
                url TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                title TEXT,
                fingerprint TEXT,
                content_chars INTEGER NOT NULL DEFAULT 0,
                http_status INTEGER
            );
            CREATE TABLE IF NOT EXISTS method_evidence(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                method_id TEXT NOT NULL,
                url TEXT NOT NULL,
                domain TEXT NOT NULL,
                evidence_score REAL NOT NULL,
                evidence_json TEXT NOT NULL,
                marketing_claim_flag INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                UNIQUE(method_id, url)
            );
            CREATE TABLE IF NOT EXISTS demo_results(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                method_id TEXT NOT NULL,
                result TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_evidence_method ON method_evidence(method_id);
            """
        )
        self.conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def add_source(self, url: str, language: str) -> bool:
        domain = _domain(url)
        now = time.time()
        row = self.conn.execute("SELECT domain FROM sources WHERE domain=?", (domain,)).fetchone()
        if row:
            self.conn.execute("UPDATE sources SET last_seen=?, language=COALESCE(language,?), homepage=COALESCE(homepage,?) WHERE domain=?",
                              (now, language, url, domain))
            self.conn.commit()
            return False
        self.conn.execute("INSERT INTO sources(domain,first_seen,last_seen,language,homepage) VALUES(?,?,?,?,?)",
                          (domain, now, now, language, url))
        self.conn.commit()
        return True

    def source_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0])

    def pages_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0])

    def page_seen(self, url: str) -> bool:
        return self.conn.execute("SELECT 1 FROM pages WHERE url=?", (url,)).fetchone() is not None

    def save_page(self, url: str, title: str, fingerprint: str, chars: int, status: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO pages(url,domain,fetched_at,title,fingerprint,content_chars,http_status) VALUES(?,?,?,?,?,?,?)",
            (url, _domain(url), time.time(), title, fingerprint, chars, status),
        )
        self.conn.execute("UPDATE sources SET pages_scanned=pages_scanned+1,last_seen=? WHERE domain=?",
                          (time.time(), _domain(url)))
        self.conn.commit()

    def save_evidence(self, item: dict) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO method_evidence
            (method_id,url,domain,evidence_score,evidence_json,marketing_claim_flag,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (item["method_id"], item["url"], item["domain"], item["evidence_score"],
             json.dumps(item, ensure_ascii=False), int(item["marketing_claim_flag"]), time.time()),
        )
        self.conn.commit()

    def method_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT method_id,
                      COUNT(DISTINCT domain) AS domains,
                      AVG(evidence_score) AS avg_score,
                      SUM(CASE WHEN marketing_claim_flag=0 THEN 1 ELSE 0 END) AS clean_evidence
               FROM method_evidence
               GROUP BY method_id
               ORDER BY domains DESC, avg_score DESC"""
        ).fetchall()

    def add_demo_result(self, method_id: str, result: str) -> None:
        result = result.upper()
        if result not in {"WIN", "LOSS", "TIE"}:
            return
        self.conn.execute("INSERT INTO demo_results(method_id,result,created_at) VALUES(?,?,?)",
                          (method_id, result, time.time()))
        self.conn.commit()

    def demo_stats(self, method_id: str) -> dict:
        rows = self.conn.execute(
            "SELECT result,COUNT(*) n FROM demo_results WHERE method_id=? GROUP BY result",
            (method_id,),
        ).fetchall()
        d={r["result"]:int(r["n"]) for r in rows}
        total=sum(d.values())
        return {"samples":total,"wins":d.get("WIN",0),"losses":d.get("LOSS",0),"ties":d.get("TIE",0),
                "win_rate":round(100*d.get("WIN",0)/max(1,total),2)}

    def close(self) -> None:
        self.conn.close()


class SelfLearningEngine:
    def __init__(self) -> None:
        self.db=LearningDB(DB_PATH)
        self.started_at=float(self.db.get_meta("started_at", str(time.time())))
        self.db.set_meta("started_at", str(self.started_at))
        self.robot_cache: dict[str, tuple[float, RobotFileParser | None]]={}
        self.queue: asyncio.Queue[tuple[str,str,str]] = asyncio.Queue()
        self.enqueued=set()
        self.metrics={"discovered":0,"fetched":0,"errors":0,"evidence":0,"runs":0}

    def day(self) -> int:
        elapsed=max(0,time.time()-self.started_at)
        return min(LEARNING_DAYS, int(elapsed//86400)+1)

    def stage(self) -> str:
        stages=[
            "M1 foundations + market structure",
            "candlesticks + price action",
            "EMA/RSI/MACD/Stochastic",
            "VWAP/ATR/volatility",
            "breakout + retest",
            "pullback + continuation",
            "reversal + divergence",
            "support/resistance + liquidity",
            "session + timing behaviour",
            "false signals + no-trade filters",
            "cross-method comparison",
            "asset/session specialization",
            "demo-result validation",
            "own-strategy synthesis",
            "final consolidation + audit",
        ]
        return stages[self.day()-1]

    def status(self) -> dict:
        domains=self.db.source_count()
        pages=self.db.pages_count()
        return {
            "enabled": os.getenv("SELF_LEARNING_ENABLED","true").strip().lower()!="false",
            "program_day": self.day(),
            "program_total_days": LEARNING_DAYS,
            "stage": self.stage(),
            "target_sites": TARGET_SITES,
            "unique_sites": domains,
            "progress_pct": round(100*domains/TARGET_SITES,2),
            "daily_target": TARGET_DAILY,
            "pages_scanned": pages,
            "queue_depth": self.queue.qsize(),
            "metrics": dict(self.metrics),
        }

    def _enqueue(self, url: str, language: str, source: str) -> None:
        u=_clean_url(url)
        if not u:
            return
        key=u+"|"+language
        if key in self.enqueued or self.db.page_seen(u):
            return
        self.enqueued.add(key)
        try:
            self.queue.put_nowait((u,language,source))
            self.metrics["discovered"]+=1
        except asyncio.QueueFull:
            pass

    async def _robots_ok(self, url: str) -> bool:
        domain=_domain(url)
        now=time.time()
        cached=self.robot_cache.get(domain)
        if cached and now-cached[0]<21600:
            rp=cached[1]
            return True if rp is None else rp.can_fetch(USER_AGENT,url)
        robots_url=f"https://{domain}/robots.txt"
        rp=RobotFileParser()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0,connect=3.0),headers={"User-Agent":USER_AGENT},follow_redirects=True) as client:
                r=await client.get(robots_url)
                if r.status_code>=400:
                    rp=None
                else:
                    rp.parse(r.text.splitlines())
        except Exception:
            rp=None
        self.robot_cache[domain]=(now,rp)
        return True if rp is None else rp.can_fetch(USER_AGENT,url)

    async def _fetch_text(self, client:httpx.AsyncClient,url:str) -> tuple[str,str,int, list[tuple[str,str]]]:
        r=await client.get(url,headers={"User-Agent":USER_AGENT,"Accept":"text/html,application/xhtml+xml"},follow_redirects=True)
        r.raise_for_status()
        ctype=r.headers.get("content-type","").lower()
        if "text/html" not in ctype and "application/xhtml" not in ctype:
            return "","",r.status_code,[]
        raw=r.content[:MAX_PAGE_BYTES]
        text_content=raw.decode(r.encoding or "utf-8","ignore")
        parser=PageParser()
        parser.feed(text_content)
        title=" ".join(parser.title_parts).strip()
        body=" ".join(parser.text_parts).strip()
        return title,body,r.status_code,parser.links

    async def _crawl_one(self, item:tuple[str,str,str]) -> None:
        url,language,origin=item
        if not await self._robots_ok(url):
            return
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT,connect=4.0)) as client:
                title,body,status,links=await self._fetch_text(client,url)
            if not body:
                return
            fp=_content_fingerprint(body)
            self.db.save_page(url,title,fp,len(body),status)
            self.db.add_source(url,language)
            self.metrics["fetched"]+=1
            for evidence in _extract_candidates(body,title,url):
                self.db.save_evidence(evidence)
                self.metrics["evidence"]+=1

            # Limited same-site expansion. This discovers more pages without
            # allowing one domain to dominate the 10,000-site target.
            base_domain=_domain(url)
            added=0
            for href,label in links:
                if added>=3:
                    break
                target=_clean_url(href,url)
                if not target or _domain(target)!=base_domain:
                    continue
                path=urlparse(target).path.lower()
                if any(x in path for x in ("/login","/signup","/register","/account","/checkout")):
                    continue
                if not any(k in (target+" "+label).lower() for k in (
                    "scalp","strategy","1-minute","1minute","m1","price-action","candlestick",
                    "ema","rsi","vwap","breakout","pullback","retest","market-structure"
                )):
                    continue
                self._enqueue(target,language,"internal")
                added+=1
        except Exception as e:
            self.metrics["errors"]+=1
            log.debug("LEARNING_FETCH_FAILED url=%s error=%s",url,e)

    async def _discover_query(self, query:str,language:str) -> int:
        found=0
        engines=[
            f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
            f"https://www.google.com/search?q={quote_plus(query)}&num=20",
        ]
        for endpoint in engines:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10.0,connect=4.0),headers={"User-Agent":USER_AGENT},follow_redirects=True) as client:
                    r=await client.get(endpoint)
                if r.status_code>=400:
                    continue
                parser=PageParser()
                parser.feed(r.text)
                for href,label in parser.links:
                    target=_clean_url(href)
                    if not target:
                        continue
                    domain=_domain(target)
                    if domain in {"google.com","googleusercontent.com","duckduckgo.com","bing.com"}:
                        continue
                    self._enqueue(target,language,"search")
                    found+=1
                if found:
                    break
            except Exception as e:
                self.metrics["errors"]+=1
                log.debug("LEARNING_SEARCH_FAILED engine=%s query=%s error=%s",endpoint,query,e)
        return found

    def synthesize(self) -> dict:
        methods=[]
        for row in self.db.method_rows():
            stats=self.db.demo_stats(row["method_id"])
            # Web evidence is never treated as profitability proof. Candidate
            # promotion requires independent sources plus demo validation.
            eligible_web=int(row["domains"])>=3 and float(row["avg_score"])>=0.55 and int(row["clean_evidence"])>=3
            demo_validated=stats["samples"]>=20 and stats["win_rate"]>50.0
            methods.append({
                "method_id":row["method_id"],
                "independent_domains":int(row["domains"]),
                "avg_evidence_score":round(float(row["avg_score"]),3),
                "clean_evidence":int(row["clean_evidence"]),
                "demo":stats,
                "status":"DEMO_VALIDATED" if demo_validated else ("RESEARCH_CANDIDATE" if eligible_web else "WATCH"),
                "active_live_brain":False,
            })
        result={
            "program":{
                "days":LEARNING_DAYS,
                "target_sites":TARGET_SITES,
                "started_at":self.started_at,
                "day":self.day(),
                "stage":self.stage(),
            },
            "rules":{
                "web_claims_are_not_proof":True,
                "minimum_independent_domains":3,
                "demo_samples_for_promotion":20,
                "auto_trade":False,
            },
            "methods":methods,
            "status":self.status(),
        }
        tmp=STATE_JSON+".tmp"
        with open(tmp,"w",encoding="utf-8") as f:
            json.dump(result,f,ensure_ascii=False,indent=2)
        os.replace(tmp,STATE_JSON)
        return result

    async def run_once(self) -> dict:
        self.metrics["runs"]+=1
        # Adaptive discovery: near the requested 15-day rate, favor search;
        # once the queue is healthy, spend cycles on evidence extraction.
        current=self.db.source_count()
        remaining=max(0,TARGET_SITES-current)
        hours_left=max(1,(LEARNING_DAYS-(self.day()-1))*24)
        target_per_hour=max(8,math.ceil(remaining/hours_left))

        languages=list(LANGUAGE_QUERIES)
        # deterministic rotation by program day and run count
        start=(self.day()*3+self.metrics["runs"])%len(languages)
        chosen=[languages[(start+i)%len(languages)] for i in range(min(6,len(languages)))]
        per_lang=max(1,min(4,math.ceil(target_per_hour/40)))
        for lang in chosen:
            qs=LANGUAGE_QUERIES[lang]
            offset=(self.metrics["runs"]+self.day())%len(qs)
            for j in range(min(per_lang,len(qs))):
                await self._discover_query(qs[(offset+j)%len(qs)],lang)

        workers=[]
        while not self.queue.empty() and len(workers)<MAX_CONCURRENCY*2:
            workers.append(asyncio.create_task(self._crawl_one(await self.queue.get())))
        if workers:
            await asyncio.gather(*workers,return_exceptions=True)

        snapshot=self.synthesize()
        log.info(
            "SELF_LEARNING_PROGRESS day=%d stage=%s sites=%d/%d pages=%d evidence=%d queue=%d",
            self.day(),self.stage(),self.db.source_count(),TARGET_SITES,
            self.db.pages_count(),len(snapshot["methods"]),self.queue.qsize()
        )
        return snapshot

    async def loop(self) -> None:
        if os.getenv("SELF_LEARNING_ENABLED","true").strip().lower()=="false":
            log.info("SELF_LEARNING_DISABLED")
            return
        while True:
            try:
                await self.run_once()
            except Exception:
                log.exception("SELF_LEARNING_RUN_FAILED")
            await asyncio.sleep(RUN_INTERVAL_SECONDS)

    def record_demo_result(self, method_id:str,result:str) -> None:
        self.db.add_demo_result(method_id,result)


LEARNING_ENGINE=SelfLearningEngine()

def learning_status() -> dict:
    return LEARNING_ENGINE.status()

def record_demo_result(method_id: str, result: str) -> None:
    LEARNING_ENGINE.record_demo_result(method_id, result)

async def self_learning_loop() -> None:
    await LEARNING_ENGINE.loop()
