"""
US Market Intel - live PC window + pop-up alerts for NQ, ES and YM.
Watches macro news, top-10 companies and every sector (pre-market and live).
No extra installs: standard Python 3.10+ on Windows.

Run:   py intel_bot.py          (live window)
Test:  py intel_bot.py --test   (checks every source and shows a test pop-up)
"""
import base64, configparser, concurrent.futures as cf, json, queue, re, subprocess, sys, threading
import time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).parent
CONFIG_FILE = HERE / "config.ini"
STATE_FILE = HERE / "state.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) USMarketIntel/2.0"

# ================= what we watch =================
INDEXES = {"NQ": "NQ=F", "ES": "ES=F", "YM": "YM=F"}
SECTORS = {"Chips": "SMH", "Tech": "XLK", "Communication": "XLC", "Consumer (discretionary)": "XLY",
           "Banks / financials": "XLF", "Energy": "XLE", "Health": "XLV", "Industrials": "XLI",
           "Consumer staples": "XLP", "Utilities": "XLU", "Materials": "XLB", "Real estate": "XLRE",
           "Small caps": "IWM"}
TOP10 = {"Nvidia": "NVDA", "Microsoft": "MSFT", "Apple": "AAPL", "Alphabet": "GOOGL",
         "Amazon": "AMZN", "Meta": "META", "Broadcom": "AVGO", "Tesla": "TSLA"}
MACRO_PRICES = {"10-year yield": "^TNX", "US dollar": "DX-Y.NYB", "Oil (WTI)": "CL=F", "VIX": "^VIX"}
INDEX_LEVELS = [1.0, 2.0, 3.0]     # % move vs yesterday's close -> alarm
SECTOR_LEVELS = [1.0, 2.0, 3.0]
STOCK_LEVELS = [2.0, 3.0, 5.0]

COMPANY_WORDS = ["nvidia", "nvda", "microsoft", "msft", "apple", "aapl", "alphabet", "google",
                 "amazon", "amzn", "meta platforms", "meta", "broadcom", "avgo", "tesla", "tsla"]
MACRO_WORDS = ["fed", "federal reserve", "fomc", "warsh", "powell", "rate hike", "rate cut",
               "interest rate", "rates", "cpi", "ppi", "inflation", "payrolls", "jobs report",
               "jobless claims", "unemployment", "gdp", "retail sales", "pmi", "ism", "treasury",
               "yields", "bond", "auction", "tariff", "tariffs", "sanctions", "iran", "china",
               "russia", "war", "ceasefire", "strike", "oil", "opec", "recession", "shutdown",
               "senate", "congress", "white house", "trump", "dollar"]
MARKET_WORDS = ["chip", "chips", "chipmakers", "semiconductor", "semiconductors", "ai stocks",
                "data center", "data centers", "tech stocks", "software stocks", "bank stocks",
                "banks", "regional banks", "energy stocks", "oil stocks", "retail stocks",
                "small caps", "nasdaq", "s&p 500", "dow", "stock futures", "futures", "wall street",
                "stocks", "selloff", "sell-off", "plunge", "plunges", "tumble", "tumbles", "slide",
                "slides", "sinks", "drops", "rally", "rallies", "surge", "surges", "jumps", "soars"]
HEAVY_WORDS = ["guidance", "outlook", "downgrade", "upgrade", "investigation", "probe", "lawsuit",
               "antitrust", "halt", "halted", "misses", "beats", "warns", "warning", "ban",
               "export controls", "layoffs", "default", "bankruptcy", "emergency", "breaking",
               "unexpectedly", "surprise", "record", "miss", "missing", "falls short", "cuts forecast",
               "raises forecast", "slump", "slumps", "crash", "crashes"]

def g_news(q):
    return ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:1h")
            + "&hl=en-US&gl=US&ceid=US:en")

# name, url, check every N seconds, official (always relevant)?
FEEDS = [
    ("Fed", "https://www.federalreserve.gov/feeds/press_all.xml", 5, True),
    ("Fed speeches", "https://www.federalreserve.gov/feeds/speeches.xml", 15, True),
    ("BLS", "https://www.bls.gov/feed/bls_latest.rss", 5, True),
    ("BEA", "https://apps.bea.gov/rss/rss.xml", 10, True),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_bulletins", 4, False),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse", 4, False),
    ("investingLive", "https://investinglive.com/feed", 3, False),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html", 8, False),
    ("CNBC markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html", 8, False),
    ("News", g_news("\"stock futures\" OR premarket OR nasdaq OR \"dow jones\" OR \"s&p 500\""), 20, False),
    ("News", g_news("nvidia OR semiconductor OR chipmakers OR \"AI stocks\" OR \"data center\""), 20, False),
    ("News", g_news("microsoft OR apple OR alphabet OR amazon OR meta OR broadcom OR tesla"), 20, False),
    ("News", g_news("\"federal reserve\" OR \"treasury yields\" OR \"jobs report\" OR inflation"), 20, False),
    ("News", g_news("tariffs OR sanctions OR iran OR china OR \"export controls\""), 20, False),
    ("News", g_news("\"bank stocks\" OR \"oil prices\" OR \"energy stocks\" OR \"software stocks\""), 45, False),
    ("News", g_news("\"retail stocks\" OR \"consumer spending\" OR \"small caps\" OR \"health stocks\""), 45, False),
]
SEC_CIKS = {"Nvidia": "0001045810", "Microsoft": "0000789019", "Apple": "0000320193",
            "Alphabet": "0001652044", "Amazon": "0001018724", "Meta": "0001326801",
            "Broadcom": "0001730168", "Tesla": "0001318605"}

def _rx(words):
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)

RX = {"company": _rx(COMPANY_WORDS), "macro": _rx(MACRO_WORDS),
      "market": _rx(MARKET_WORDS), "heavy": _rx(HEAVY_WORDS)}

# ================= time (New York) =================
def ny_now():
    utc = datetime.now(timezone.utc)
    def nth_sunday(y, m, n):
        d = datetime(y, m, 1, tzinfo=timezone.utc)
        return d + timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))
    start = nth_sunday(utc.year, 3, 2) + timedelta(hours=7)
    end = nth_sunday(utc.year, 11, 1) + timedelta(hours=6)
    return utc.astimezone(timezone(timedelta(hours=-4 if start <= utc < end else -5)))

def in_session(cfg):
    now = ny_now()
    return now.weekday() < 5 and cfg["settings"]["start_ny"] <= now.strftime("%H:%M") <= cfg["settings"]["end_ny"]

# ================= web =================
_COND = {}   # url -> (etag, last-modified, body): ask "anything new?" so fast polling stays light

def http_get(url, headers=None, timeout=10):
    h = {"User-Agent": UA, **(headers or {})}
    c = _COND.get(url)
    if c:
        if c[0]:
            h["If-None-Match"] = c[0]
        if c[1]:
            h["If-Modified-Since"] = c[1]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            body = r.read()
            et, lm = r.headers.get("ETag"), r.headers.get("Last-Modified")
            if (et or lm) and len(_COND) < 400:
                _COND[url] = (et, lm, body)
            return body
    except urllib.error.HTTPError as e:
        if e.code == 304 and c:                  # nothing new since last time
            return c[2]
        raise

def http_post_json(url, payload, headers=None, timeout=20):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

# ================= news =================
def parse_time(text):
    if not text:
        return None
    for fn in (lambda t: parsedate_to_datetime(t), lambda t: datetime.fromisoformat(t.replace("Z", "+00:00"))):
        try:
            return fn(text).astimezone(timezone.utc)
        except Exception:
            pass
    return None

def parse_feed(raw, source):
    items, atom = [], "{http://www.w3.org/2005/Atom}"
    root = ET.fromstring(raw)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = it.findtext("source") or source
        if source == "News" and " - " in title:
            title, src = title.rsplit(" - ", 1)
        items.append({"id": it.findtext("guid") or it.findtext("link") or title, "title": title,
                      "link": (it.findtext("link") or "").strip(), "source": src.strip(),
                      "time": parse_time(it.findtext("pubDate"))})
    for e in root.iter(atom + "entry"):
        link = e.find(atom + "link")
        items.append({"id": e.findtext(atom + "id") or e.findtext(atom + "title"),
                      "title": (e.findtext(atom + "title") or "").strip(),
                      "link": link.get("href") if link is not None else "", "source": source,
                      "time": parse_time(e.findtext(atom + "updated"))})
    return [i for i in items if i["title"]]

def score(title, always):
    if always:
        return 10
    macro = {m.lower() for m in RX["macro"].findall(title)}
    s = (2 if RX["company"].search(title) else 0) + (2 if macro else 0) + (1 if len(macro) >= 2 else 0)
    s += 1 if RX["market"].search(title) else 0
    s += 1 if RX["heavy"].search(title) else 0
    return s

def kind_of(title, source):
    if source.startswith(("Fed", "BLS", "BEA")):
        return "MACRO"
    if source.startswith("SEC") or RX["company"].search(title):
        return "COMPANY"
    return "MACRO" if RX["macro"].search(title) else "SECTOR"

TOPIC_SYMBOLS = [  # headline words -> the price that should confirm it
    (_rx(["chip", "chips", "chipmakers", "semiconductor", "semiconductors", "nvidia", "broadcom",
          "ai stocks", "data center", "data centers"]), "SMH"),
    (_rx(["bank", "banks", "bank stocks", "regional banks", "lenders"]), "XLF"),
    (_rx(["oil", "opec", "crude", "energy stocks"]), "XLE"),
    (_rx(["software", "software stocks", "tech stocks", "cloud"]), "XLK"),
    (_rx(["retail", "retail stocks", "consumer spending", "amazon", "tesla"]), "XLY"),
    (_rx(["small caps", "russell"]), "IWM"),
]
OFFICIAL_BIG = _rx(["employment situation", "payroll", "payrolls", "consumer price index", "cpi",
                    "producer price", "gdp", "gross domestic product", "fomc statement",
                    "federal reserve issues", "monetary policy", "rate", "pce", "personal income"])

def impact(title, source, always, qs):
    """How likely this headline leads the market (higher = bigger)."""
    imp = score(title, False)
    if always and OFFICIAL_BIG.search(title):
        imp += 7
    if RX["heavy"].search(title) and (RX["company"].search(title) or RX["market"].search(title)):
        imp += 2
    syms = [sym for rx, sym in TOPIC_SYMBOLS if rx.search(title)]
    syms += [sym for n, sym in TOP10.items() if re.search(r"\b" + re.escape(n.lower()) + r"\b", title.lower())]
    if any(abs(pct(qs.get(s))) >= 2 for s in syms if s in qs):
        imp += 4                                  # price already confirms the story
    if any(abs(pct(qs[s])) >= 1 for s in INDEXES.values() if s in qs):
        imp += 1
    import econ_calendar
    return imp + econ_calendar.boost(sys.modules[__name__], title, "us")

def words(t):
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 3}

def is_duplicate(title, recent):
    w = words(title)
    return any(w and r and len(w & set(r)) / len(w | set(r)) > 0.5 for r in recent)

# ================= prices (Yahoo, free, may be delayed) =================
def quote(sym):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym)
           + "?interval=5m&range=1d&includePrePost=true")
    d = json.loads(http_get(url))["chart"]["result"][0]
    prev = d["meta"].get("chartPreviousClose") or d["meta"].get("previousClose")
    closes = [c for c in d["indicators"]["quote"][0]["close"] if c is not None]
    return (closes[-1] if closes else d["meta"]["regularMarketPrice"]), prev

def pct(q):
    return (q[0] / q[1] - 1) * 100 if q and q[1] else 0.0

class Prices:
    def __init__(self, seconds=30):
        self.seconds, self.t, self.data, self.lock = seconds, 0, {}, threading.Lock()
    def refresh(self, force=False):
        if not force and time.time() - self.t < self.seconds:
            return self.data
        syms = [*INDEXES.values(), *SECTORS.values(), *TOP10.values(), *MACRO_PRICES.values()]
        out = {}
        with cf.ThreadPoolExecutor(12) as ex:
            futs = {ex.submit(quote, s): s for s in syms}
            for f in cf.as_completed(futs):
                try:
                    out[futs[f]] = f.result()
                except Exception:
                    pass
        if out:
            with self.lock:
                self.data, self.t = out, time.time()
        return self.data
    def get(self):
        with self.lock:
            return dict(self.data)

def index_line(qs):
    parts = [f"{n} {pct(qs[s]):+.1f}%" for n, s in INDEXES.items() if s in qs]
    return " | ".join(parts) if parts else "prices unavailable"

def sector_ranking(qs):
    return sorted(((n, pct(qs[s])) for n, s in SECTORS.items() if s in qs), key=lambda x: -x[1])

# ================= earnings (top-10 only, Nasdaq calendar, free) =================
WHEN = {"time-pre-market": "before the open", "time-after-hours": "after the close"}

def nasdaq_earnings(day):
    url = f"https://api.nasdaq.com/api/calendar/earnings?date={day:%Y-%m-%d}"
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/130.0 Safari/537.36", "Accept": "application/json, text/plain, */*",
           "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
    j = json.loads(http_get(url, hdr))
    return ((j.get("data") or {}).get("rows")) or []

class Earnings:
    def __init__(self, hours=4):
        self.seconds, self.t, self.items, self.status = hours * 3600, 0, [], "loading..."
    def refresh(self, force=False):
        if not force and time.time() - self.t < self.seconds:
            return self.items
        today = ny_now().date()
        days = [today + timedelta(days=i) for i in range(15) if (today + timedelta(days=i)).weekday() < 5]
        names = {sym: n for n, sym in TOP10.items()}
        names.setdefault("GOOG", names.get("GOOGL", "Alphabet"))
        found, ok = [], 0
        with cf.ThreadPoolExecutor(5) as ex:
            for day, rows in zip(days, ex.map(lambda d: self._safe(d), days)):
                if rows is None:
                    continue
                ok += 1
                for r in rows:
                    sym = (r.get("symbol") or "").upper()
                    if sym in names and not any(f["name"] == names[sym] for f in found):
                        found.append({"date": day.isoformat(), "name": names[sym], "symbol": sym,
                                      "when": WHEN.get(r.get("time", ""), "time not set yet"),
                                      "eps": r.get("epsForecast") or "n/a", "last": r.get("lastYearEPS") or "n/a",
                                      "ests": r.get("noOfEsts") or "?"})
        if ok:
            self.items, self.t = sorted(found, key=lambda f: f["date"]), time.time()
            self.status = "ok"
        else:
            self.status = "earnings calendar unavailable"
        return self.items
    @staticmethod
    def _safe(day):
        try:
            return nasdaq_earnings(day)
        except Exception:
            return None

def ai_expect(cfg, e):
    key = cfg["ai"].get("anthropic_api_key", "").strip()
    if not key:
        return ""
    prompt = (f"{e['name']} ({e['symbol']}) reports earnings {e['when']}. Profit estimate {e['eps']} per share "
              f"(a year ago {e['last']}). In max 2 very short lines, simple words, for an NQ/ES/YM futures "
              "trader: what the market will focus on in this report, and when the index reaction is likely. "
              "No advice, no predictions of the result.")
    try:
        r = http_post_json("https://api.anthropic.com/v1/messages",
                           {"model": cfg["ai"].get("model", "claude-haiku-5-5"), "max_tokens": 120,
                            "messages": [{"role": "user", "content": prompt}]},
                           {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return r["content"][0]["text"].strip()
    except Exception:
        return ""

def earnings_line(e):
    d = datetime.fromisoformat(e["date"])
    return f"{d:%a %d %b}  ·  {e['name']}  ·  {e['when']}  ·  profit est. {e['eps']}"

# ================= outputs =================
def toast(title, body):
    """Windows pop-up notification (bottom-right) using built-in PowerShell."""
    if sys.platform != "win32":
        return
    esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                     .replace('"', "&quot;").replace("'", "&apos;"))
    xml = (f'<toast><visual><binding template="ToastGeneric"><text>{esc(title)}</text>'
           f'<text>{esc(body)}</text></binding></visual>'
           '<audio src="ms-winsoundevent:Notification.Default"/></toast>')
    ps = ("[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
          "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]|Out-Null;"
          f"$d=New-Object Windows.Data.Xml.Dom.XmlDocument;$d.LoadXml('{xml}');"
          "$t=New-Object Windows.UI.Notifications.ToastNotification $d;"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
          "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($t)")
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-EncodedCommand", enc], creationflags=0x08000000)
    except Exception as e:
        print("Pop-up failed:", e)

def tg_send(cfg, text):
    token, chat = cfg["telegram"].get("bot_token", "").strip(), cfg["telegram"].get("chat_id", "").strip()
    if not token or not chat:
        return False
    try:
        http_post_json(f"https://api.telegram.org/bot{token}/sendMessage",
                       {"chat_id": chat, "text": text, "disable_web_page_preview": True})
        return True
    except Exception as e:
        print("Telegram failed:", e)
        return False

def ai_note(cfg, title, source, moves):
    key = cfg["ai"].get("anthropic_api_key", "").strip()
    if not key:
        return ""
    prompt = (f"Headline: {title}\nSource: {source}\nFutures now: {moves}\n\n"
              "You help an intraday trader of NQ, ES and YM futures. If this headline is unlikely to move "
              "US index futures or a major sector today, reply only SKIP. Otherwise start with 'IMPACT n' "
              "(n = 1 small to 5 can lead the whole market), then max 2 very short lines, simple words: "
              "1) why it matters, 2) which of NQ / ES / YM (or which sector) is most affected and which "
              "direction. No advice, no entries.")
    try:
        r = http_post_json("https://api.anthropic.com/v1/messages",
                           {"model": cfg["ai"].get("model", "claude-haiku-5-5"), "max_tokens": 120,
                            "messages": [{"role": "user", "content": prompt}]},
                           {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return r["content"][0]["text"].strip()
    except Exception as e:
        print("AI note failed:", e)
        return ""

# ================= engine =================
def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"seen": [], "recent": [], "alarms": [], "followups": [], "seeded": False,
                "hello": "", "snaps": []}

def save_state(st):
    for k, n in (("seen", 5000), ("recent", 200), ("alarms", 800), ("snaps", 50), ("earn", 100), ("top", 20), ("econ", 100)):
        st[k] = st.get(k, [])[-n:]
    STATE_FILE.write_text(json.dumps(st))

class Engine:
    def __init__(self, cfg, out_queue=None):
        self.cfg, self.q, self.st = cfg, out_queue, load_state()
        self.st.setdefault("snaps", [])
        self.st.setdefault("earn", [])
        self.prices, self.last_poll, self.status = Prices(), {}, "Starting..."
        self._news_lock = threading.RLock()
        self.earnings = Earnings()
        self.core = sys.modules[__name__]
        self.st.setdefault("tone", [])
        self.st.setdefault("top", [])
        self.web_alerts = []          # same alerts, for the iPhone page

    def top_today(self):
        day = ny_now().strftime("%Y-%m-%d")
        return [t for t in self.st["top"] if t["day"] == day]

    def add_top(self, kind, title, line, imp):
        day = ny_now().strftime("%Y-%m-%d")
        self.st["top"] = [t for t in self.st["top"] if t["day"] == day]
        self.st["top"].append({"day": day, "time": ny_now().strftime("%H:%M"), "kind": kind,
                               "title": title, "line": line, "impact": imp})
        keep = int(self.cfg["settings"].get("top_stories", "4"))
        self.st["top"] = sorted(self.st["top"], key=lambda t: (-t["impact"], t["time"]))[:keep]

    def emit(self, kind, title, lines, link="", imp=0):
        stamp = ny_now().strftime("%H:%M")
        top = imp >= int(self.cfg["settings"].get("top_story_level", "7"))
        if top:
            self.add_top(kind, title, lines[0] if lines else "", imp)
        if self.q is not None:
            self.q.put({"time": stamp, "kind": kind, "title": title, "lines": lines, "link": link, "top": top})
        self.web_alerts.append({"time": stamp, "kind": kind, "title": title, "lines": lines, "link": link, "top": top})
        del self.web_alerts[:-150]
        if self.cfg["settings"].get("popups", "yes").lower().startswith("y"):
            toast(("TOP STORY · " if top else "") + f"{kind} · {stamp} NY",
                  title + ("\n" + lines[0] if lines else ""))
        tg_send(self.cfg, f"{kind} · {stamp} NY\n{title}\n" + "\n".join(lines) + (f"\n{link}" if link else ""))
        print(f"[{stamp}] {kind}: {title}")

    def feeds(self):
        feeds = list(FEEDS)
        try:                                     # 1 min before to 3 min after a big US number: official sites every 1 s
            import econ_calendar
            if econ_calendar.get(self.core).near("us", before=1, after=3):
                feeds = [(n, u, 1 if always else iv, always) for n, u, iv, always in feeds]
        except Exception:
            pass
        email = self.cfg["sec"].get("contact_email", "").strip()
        if email:
            for name, cik in SEC_CIKS.items():
                feeds.append((f"SEC filing {name}", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                              f"&CIK={cik}&type=8-K&dateb=&owner=include&count=5&output=atom", 90, True))
        return feeds

    def fetch(self, feed):
        hdr = {"User-Agent": f"USMarketIntel {self.cfg['sec']['contact_email'].strip()}"} if "sec.gov" in feed[1] else None
        return parse_feed(http_get(feed[1], hdr, timeout=6), feed[0])

    def check_news(self, speed=None):
        """speed='fast': sources checked every 5 s or quicker (own thread); 'slow': the rest."""
        now = time.time()
        due = [f for f in self.feeds() if now - self.last_poll.get(f[1], 0) >= f[2]
               and (speed is None or (f[2] <= 5) == (speed == "fast"))]
        if not due:
            return
        results = []
        with cf.ThreadPoolExecutor(10) as ex:
            futs = {ex.submit(self.fetch, f): f for f in due}
            for fut in cf.as_completed(futs):
                f = futs[fut]
                self.last_poll[f[1]] = now
                fails = self.__dict__.setdefault("_fails", {})
                try:
                    results.append((f, fut.result()))
                    fails.pop(f[1], None)
                except Exception:          # source refused or too slow: wait longer so we don't get blocked
                    fails[f[1]] = fails.get(f[1], 0) + 1
                    self.last_poll[f[1]] = now + min(300, max(10, f[2]) * fails[f[1]])
        with self._news_lock:
            self._process_news(results, now)

    def _process_news(self, results, now):
        st, seen = self.st, set(self.st["seen"])
        max_age = timedelta(minutes=int(self.cfg["settings"].get("max_news_age_minutes", "45")))
        utc_now = datetime.now(timezone.utc)
        for (name, _, _, always), items in results:
            for it in items:
                key = it["id"] or it["title"]
                if key in seen:
                    continue
                seen.add(key)
                st["seen"].append(key)
                if not st["seeded"]:
                    continue
                if it["time"] and utc_now - it["time"] > max_age:
                    continue
                if score(it["title"], always) < 3 or is_duplicate(it["title"], st["recent"]):
                    continue
                st["recent"].append(sorted(words(it["title"])))
                try:
                    import us_opinion
                    us_opinion.record_tone(st, it["title"])
                except Exception:
                    pass
                qs = self.prices.get()           # last prices (max 30 s old) - don't wait for a new download
                moves = index_line(qs)
                note = ai_note(self.cfg, it["title"], it["source"], moves)
                if note.upper().startswith("SKIP"):
                    continue
                imp = impact(it["title"], name, always, qs)
                m = re.match(r"\s*IMPACT\s*:?\s*(\d)", note, re.I)
                if m:
                    imp += (int(m.group(1)) - 3) * 2
                    note = note[m.end():].strip()
                age = f" · {max(0, int((utc_now - it['time']).total_seconds() // 60))} min old" if it["time"] else ""
                lines = [moves, f"{it['source']}{age}"] + ([note] if note else [])
                self.emit(kind_of(it["title"], name), it["title"], lines, it["link"], imp)
                base = {s: q[0] for s, q in qs.items() if s in INDEXES.values()}
                if base:
                    st["followups"].append({"title": it["title"][:90], "due": now + 300, "base": base})
        st["seeded"] = True

    def check_followups(self):
        now, keep = time.time(), []
        for fu in self.st["followups"]:
            if now < fu["due"]:
                keep.append(fu)
                continue
            if now - fu["due"] > 600:          # stale (app was closed) - skip
                continue
            qs = self.prices.get()
            parts = [f"{n} {(qs[s][0] / fu['base'][s] - 1) * 100:+.2f}%"
                     for n, s in INDEXES.items() if s in qs and s in fu["base"]]
            if parts:
                self.emit("REACTION", fu["title"], ["5 minutes after: " + " | ".join(parts)])
        self.st["followups"] = keep

    def check_alarms(self):
        qs = self.prices.get()
        day = ny_now().strftime("%Y-%m-%d")
        for group, levels, label in ((INDEXES, INDEX_LEVELS, "INDEX"), (SECTORS, SECTOR_LEVELS, "SECTOR MOVE"),
                                     (TOP10, STOCK_LEVELS, "BIG NAME MOVE")):
            for name, sym in group.items():
                if sym not in qs:
                    continue
                p = pct(qs[sym])
                hit = [lv for lv in levels if abs(p) >= lv]
                if not hit:
                    continue
                key = f"{day}:{sym}:{'up' if p > 0 else 'down'}:{max(hit)}"
                if key in self.st["alarms"]:
                    continue
                self.st["alarms"].append(key)
                when = "pre-market" if ny_now().strftime("%H:%M") < "09:30" else "today"
                big = (label == "SECTOR MOVE" and max(hit) >= 2) or (label == "BIG NAME MOVE" and max(hit) >= 3)
                self.emit(label, f"{name} {'UP' if p > 0 else 'DOWN'} {p:+.1f}% {when}", [index_line(qs)],
                          imp=8 if big else 0)

    def check_snapshots(self):
        now = ny_now()
        hm, day = now.strftime("%H:%M"), now.strftime("%Y-%m-%d")
        for t in [x.strip() for x in self.cfg["settings"].get("sector_snapshots_ny", "").split(",") if x.strip()]:
            key = f"{day}:{t}"
            if hm >= t and key not in self.st["snaps"] and hm <= (datetime.strptime(t, "%H:%M") + timedelta(minutes=10)).strftime("%H:%M"):
                self.st["snaps"].append(key)
                rank = sector_ranking(self.prices.get())
                if rank:
                    lead = ", ".join(f"{n} {p:+.1f}%" for n, p in rank[:3])
                    lag = ", ".join(f"{n} {p:+.1f}%" for n, p in rank[-3:][::-1])
                    self.emit("SECTOR SNAPSHOT", f"Leading: {lead}", [f"Lagging: {lag}", index_line(self.prices.get())])

    def check_earnings(self):
        now = ny_now()
        if now.strftime("%H:%M") < self.cfg["settings"].get("earnings_alert_ny", "07:00"):
            return
        today = now.date()
        nxt = today + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        for e in self.earnings.items:
            label = {today.isoformat(): "EARNINGS TODAY", nxt.isoformat(): "EARNINGS TOMORROW"}.get(e["date"])
            key = f"{today}:{e['symbol']}:{label}"
            if not label or key in self.st["earn"]:
                continue
            self.st["earn"].append(key)
            watch = ai_expect(self.cfg, e) or (
                "Watch: profit vs estimate, sales and next-quarter guidance. "
                + ("Index reaction after 16:00 NY and at tomorrow's open." if e["when"] == "after the close"
                   else "Index reaction at the 09:30 NY open." if e["when"] == "before the open" else ""))
            self.emit(label, f"{e['name']} reports {e['when']}",
                      [f"Profit estimate {e['eps']} per share (a year ago {e['last']}, {e['ests']} analysts)", watch])

    def check_econ(self):
        """Every morning: today's big US news as an alert; 15 minutes before each one: COMING UP."""
        import econ_calendar
        cal = econ_calendar.get(self.core)
        now = ny_now()
        day, hm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        self.st.setdefault("econ", [])
        if hm >= self.cfg["settings"].get("earnings_alert_ny", "07:00") and f"{day}:today" not in self.st["econ"]:
            self.st["econ"].append(f"{day}:today")
            b = cal.banner("us")
            if b["today"]:
                self.emit("TODAY'S NEWS", "  ·  ".join(l["text"] for l in b["today"]),
                          [x for x in (b["why"], "This week: " + b["week"]) if x], imp=8)
            else:
                self.emit("TODAY'S NEWS", "No big US news today", ["This week: " + b["week"]])
        for g in cal.groups("us"):
            mins = (g["t"] - now).total_seconds() / 60
            key = f"{g['t']:%Y-%m-%d %H:%M}:{g['short']}"
            if g["big"] and g["short"] != "US holiday" and 0 < mins <= 15 and key not in self.st["econ"]:
                self.st["econ"].append(key)
                self.emit("COMING UP", f"{g['short']} at {g['t']:%H:%M} NY (in {int(mins)} min)",
                          [x for x in (g["why"], cal.detail(g)) if x])

    def fast_news_forever(self):
        """Own thread: the quickest sources every 1-5 s, never held up by prices or slower sites."""
        while True:
            try:
                if in_session(self.cfg) and self.st.get("seeded"):
                    self.check_news("fast")
            except Exception as e:
                print("[fast news]", e.__class__.__name__)
            time.sleep(1)

    def run_forever(self):
        secs = int(self.cfg["settings"].get("check_seconds", "15"))
        threading.Thread(target=self.fast_news_forever, daemon=True).start()
        while True:
            try:
                try:
                    import econ_calendar
                    econ_calendar.get(self.core).refresh()
                except Exception:
                    pass
                self.prices.refresh()
                self.earnings.refresh()
                if in_session(self.cfg):
                    today = ny_now().strftime("%Y-%m-%d")
                    if self.st.get("hello") != today:
                        self.st["hello"] = today
                        self.emit("LIVE", "Alerts are ON", [index_line(self.prices.get())])
                    self.status = f"Live · last check {ny_now():%H:%M:%S} NY"
                    self.check_news(None if not self.st.get("seeded") else "slow")
                    with self._news_lock:           # the fast-news thread writes to the same memory
                        self.check_alarms()
                        self.check_snapshots()
                        self.check_earnings()
                        self.check_followups()
                        self.check_econ()
                        if time.time() - getattr(self, "_saved", 0) > 15:     # write the file every 15 s, not every check
                            save_state(self.st)
                            self._saved = time.time()
                else:
                    self.status = f"Waiting · alerts start {self.cfg['settings']['start_ny']} NY (Mon-Fri)"
                time.sleep(secs)
            except Exception as e:
                self.status = f"Error, retrying: {e.__class__.__name__}"
                time.sleep(30)

# ================= window =================
GOLD, BLACK, GREY, LINE, BG = "#D4A72C", "#DADADA", "#8A8A8A", "#2E2E2E", "#161616"
DOWN = "#9A9A9A"   # down moves: soft grey on dark background

def color(p):
    return GOLD if p > 0 else DOWN

def dark_titlebar(win):
    """Dark title bar on Windows 10/11."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        win.update()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        for attr, val in ((20, 1), (19, 1), (35, 0x161616), (36, 0xDADADA)):
            v = ctypes.c_int(val)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
        win.withdraw(); win.deiconify()
    except Exception:
        pass

def run_window(cfg):
    import tkinter as tk, webbrowser
    q = queue.Queue()
    eng = Engine(cfg, q)
    threading.Thread(target=eng.run_forever, daemon=True).start()
    oil_eng = None
    try:
        import oil_intel
        oil_eng = oil_intel.OilEngine(sys.modules[__name__], cfg)
        threading.Thread(target=oil_eng.run_forever, daemon=True).start()
    except Exception as e:
        print("Oil panel not available:", e)
    metals_eng = None
    try:
        import metals_intel
        metals_eng = metals_intel.MetalsEngine(sys.modules[__name__], cfg)
        threading.Thread(target=metals_eng.run_forever, daemon=True).start()
    except Exception as e:
        print("Metals panel not available:", e)
    phone_url = ""
    if cfg["settings"].get("phone", "yes").lower().startswith("y"):
        try:
            import phone_server
            phone_url = phone_server.start(sys.modules[__name__], eng, oil_eng,
                                           int(cfg["settings"].get("phone_port", "8765")), metals_eng)
        except Exception as e:
            print("iPhone page not available:", e)

    root = tk.Tk()
    root.title("US Market Intel")
    root.geometry("470x1000")
    root.configure(bg=BG)
    dark_titlebar(root)
    root.attributes("-topmost", cfg["settings"].get("always_on_top", "yes").lower().startswith("y"))
    F = "Segoe UI"

    top = tk.Frame(root, bg=BG)
    top.pack(fill="x", padx=14, pady=(10, 2))
    tiles = {}
    for name in INDEXES:
        f = tk.Frame(top, bg=BG, highlightbackground=LINE, highlightthickness=1)
        f.pack(side="left", expand=True, fill="x", padx=3)
        tk.Label(f, text=name, bg=BG, fg=GREY, font=(F, 8)).pack(pady=(3, 0))
        tiles[name] = tk.Label(f, text="--", bg=BG, fg=BLACK, font=(F, 14, "bold"))
        tiles[name].pack(pady=(0, 4))
    macro_lbl = tk.Label(root, text="", bg=BG, fg=GREY, font=(F, 8), anchor="w")
    macro_lbl.pack(fill="x", padx=17)
    import econ_calendar
    econ_box = tk.Frame(root, bg="#221D10", highlightbackground=GOLD, highlightthickness=1)
    econ_box.pack(fill="x", padx=14, pady=(6, 0))
    econ_head = econ_calendar.make_head(tk, econ_box, F, GOLD)
    econ_head.pack(fill="x", padx=8, pady=(3, 0))
    econ_body = tk.Label(econ_box, text="", bg="#221D10", fg="#AAAAAA", font=(F, 8), anchor="w",
                         justify="left", wraplength=430)
    econ_body.pack(fill="x", padx=8, pady=(0, 4))

    drivers_row = tk.Label(root, text="Top market drivers today", bg=BG, fg=GOLD, font=(F, 10, "bold"), anchor="w")
    drivers_row.pack(fill="x", padx=17, pady=(8, 2))
    panels = {}   # side panels: OIL, METALS (each its own window, side by side)

    def screen_edges():
        """Left and right edge of the monitor the US window is on."""
        try:
            import ctypes
            from ctypes import wintypes
            class MI(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            mon = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)
            mi = MI()
            mi.cbSize = ctypes.sizeof(MI)
            ctypes.windll.user32.GetMonitorInfoW(mon, ctypes.byref(mi))
            return mi.rcWork.left, mi.rcWork.right
        except Exception:
            return 0, root.winfo_screenwidth()

    def slot_place(k):
        """Window k places next to the US window: to the right if there is room, else to the left."""
        def place():
            root.update_idletasks()
            w, h, x, y = root.winfo_width(), root.winfo_height(), root.winfo_x(), root.winfo_y()
            step = w + 2 * max(0, root.winfo_rootx() - x) + 2
            left, right = screen_edges()
            nx = x + k * step
            if nx + step > right:
                nx = max(left, x - k * step)
            return f"{w}x{h}+{nx}+{y}"
        return place

    def is_open(name):
        p = panels[name]
        return p["win"] is not None and p["win"].winfo_exists()

    def open_panel(name):
        p = panels[name]
        if p["eng"] is None:
            return
        if is_open(name):
            p["win"].deiconify()
            p["win"].lift()
            return
        used = {panels[o]["slot"] for o in panels if o != name and is_open(o)}
        p["slot"] = next(k for k in range(1, 5) if k not in used)
        others = [(o, lambda o=o: root.after(10, lambda: open_panel(o)))
                  for o in panels if o != name and panels[o]["eng"] is not None]
        p["win"] = p["open"](tk, root, p["eng"], (GOLD, BLACK, GREY, LINE, BG, DOWN), dark_titlebar, webbrowser,
                             others, slot_place(p["slot"]))
        def reset(e, name=name):
            if e.widget is panels[name]["win"]:
                try:
                    panels[name]["btn"].configure(text=f"{name}  \u25b8")
                except Exception:
                    pass
        p["win"].bind("<Destroy>", reset)
        p["btn"].configure(text=f"{name}  \u25c2")

    def toggle_panel(name):
        p = panels[name]
        if p["eng"] is None:
            return
        if is_open(name):
            p["win"].destroy()
            p["eng"].window_open = False
            p["win"] = None
            p["btn"].configure(text=f"{name}  \u25b8")
        else:
            open_panel(name)

    def add_panel(name, panel_eng, opener, x):
        btn = tk.Button(root, text=f"{name}  \u25b8", command=lambda: toggle_panel(name), bg="#221D10", fg=GOLD,
                        activebackground=GOLD, activeforeground="#161616", relief="flat", bd=0,
                        font=(F, 9, "bold"), padx=10, pady=2, cursor="hand2",
                        highlightbackground=GOLD, highlightthickness=1)
        btn.place(in_=drivers_row, relx=1.0, x=x, rely=0.5, anchor="e")
        panels[name] = {"eng": panel_eng, "win": None, "btn": btn, "open": opener, "slot": 0}

    add_panel("METALS", metals_eng, metals_intel.open_metals_window if metals_eng else None, 0)
    add_panel("OIL", oil_eng, oil_intel.open_oil_window if oil_eng else None, -96)
    pin = tk.Text(root, bg="#221D10", fg=BLACK, font=(F, 9), wrap="word", relief="flat", height=6,
                  padx=6, pady=4, highlightbackground=GOLD, highlightthickness=1)
    pin.pack(fill="x", padx=14)
    pin.tag_configure("t", font=(F, 9, "bold"))
    pin.tag_configure("s", foreground="#9A9A9A", font=(F, 8))
    pin.configure(state="disabled")
    pin_state = {"sig": None}

    op = tk.Frame(root, bg=BG)
    op.pack(fill="x", padx=14, pady=(6, 0))
    def box(title):
        b = tk.Frame(op, bg=BG, highlightbackground=LINE, highlightthickness=1)
        b.pack(fill="x", pady=(0, 5))
        head = tk.Label(b, text=title, bg=BG, fg=BLACK, font=(F, 9, "bold"), anchor="w")
        head.pack(fill="x", padx=8, pady=(3, 0))
        body = tk.Label(b, text="", bg=BG, fg="#AAAAAA", font=(F, 8), anchor="w", justify="left", wraplength=420)
        body.pack(fill="x", padx=8, pady=(0, 4))
        return head, body
    read_head, read_why = box("My read")
    read_head, read_tm = econ_calendar.read_row(tk, F, read_head, read_why)
    _, ment_lbl = box("Mentor's desk note (if this, then that)")

    tk.Label(root, text="Sectors vs yesterday's close", bg=BG, fg=GREY, font=(F, 9), anchor="w").pack(fill="x", padx=17, pady=(10, 2))
    board = tk.Frame(root, bg=BG)
    board.pack(fill="x", padx=17)
    rows = []
    for _ in SECTORS:
        r = tk.Frame(board, bg=BG)
        r.pack(fill="x")
        a = tk.Label(r, text="", bg=BG, fg=BLACK, font=(F, 9), anchor="w", width=22)
        a.pack(side="left")
        c = tk.Canvas(r, width=150, height=10, bg=BG, highlightthickness=0)
        c.pack(side="left", padx=4)
        b = tk.Label(r, text="", bg=BG, fg=BLACK, font=(F, 9, "bold"), anchor="e", width=7)
        b.pack(side="right")
        rows.append((a, c, b))

    tk.Label(root, text="Big-name earnings (next 2 weeks)", bg=BG, fg=GREY, font=(F, 9), anchor="w").pack(fill="x", padx=17, pady=(12, 2))
    earn_lbl = tk.Label(root, text="loading...", bg=BG, fg=BLACK, font=(F, 9), anchor="w", justify="left")
    earn_lbl.pack(fill="x", padx=17)

    tk.Label(root, text="Alerts (newest on top)", bg=BG, fg=GREY, font=(F, 9), anchor="w").pack(fill="x", padx=17, pady=(12, 2))
    feed = tk.Text(root, bg=BG, fg=BLACK, font=(F, 9), wrap="word", relief="flat", padx=4,
                   highlightbackground=LINE, highlightthickness=1)
    feed.pack(fill="both", expand=True, padx=14)
    feed.tag_configure("kind", font=(F, 8, "bold"), foreground=GREY)
    feed.tag_configure("title", font=(F, 10, "bold"))
    feed.tag_configure("line", foreground="#AAAAAA")
    feed.tag_configure("link", foreground=GREY, underline=True)
    feed.configure(state="disabled")

    status = tk.Label(root, text="", bg=BG, fg=GREY, font=(F, 8), anchor="w")
    status.pack(fill="x", padx=17, pady=(4, 8))
    links = {}

    def add_alert(a):
        feed.configure(state="normal")
        feed.insert("1.0", "\n")
        if a["link"]:
            tag = f"l{len(links)}"
            links[tag] = a["link"]
            feed.insert("1.0", "open article\n", ("link", tag))
            feed.tag_bind(tag, "<Button-1>", lambda e, u=a["link"]: webbrowser.open(u))
        for ln in reversed(a["lines"]):
            feed.insert("1.0", ln + "\n", "line")
        feed.insert("1.0", a["title"] + "\n", "title")
        feed.insert("1.0", f"{a['time']} NY  ·  {a['kind']}\n", "kind")
        feed.configure(state="disabled")

    def draw_pins():
        items = eng.top_today()
        sig = json.dumps(items)
        if sig == pin_state["sig"]:
            return
        pin_state["sig"] = sig
        pin.configure(state="normal", height=max(2, sum(2 + len(t["title"]) // 50 for t in items)))
        pin.delete("1.0", "end")
        if not items:
            pin.insert("end", "Nothing big yet today. Market-leading news and big sector moves appear here.", "s")
        for t in sorted(items, key=lambda t: t["time"], reverse=True):
            pin.insert("end", f"{t['time']} NY  {t['title']}\n", "t")
            pin.insert("end", f"{t['kind']} · {t['line']}\n", "s")
        pin.configure(state="disabled")

    def tick():
        while not q.empty():
            add_alert(q.get())
        draw_pins()
        if int(time.time()) % 5 == 0:
            for e in (oil_eng, metals_eng):
                try:
                    e and e.my_read()
                except Exception:
                    pass
        qs = eng.prices.get()
        for name, sym in INDEXES.items():
            if sym in qs:
                p = pct(qs[sym])
                tiles[name].configure(text=f"{'▲' if p > 0 else '▼'} {p:+.2f}%", fg=color(p))
        try:
            import us_opinion
            r = us_opinion.my_read(eng)
            head_txt, tm_txt = econ_calendar.hi_title(r)
            read_tm.configure(text=tm_txt)
            read_head.configure(text=head_txt, font=(F, 11 if r.get("hi_since") else 9, "bold"),
                                fg=GOLD if r["bias"] == "UP" else DOWN if r["bias"] == "DOWN" else BLACK)
            read_why.configure(text=(r["lead"] + "\n" if r["lead"] else "") + "\n".join("• " + w for w in r["why"]))
            ment_lbl.configure(text="\n".join("• " + m for m in us_opinion.mentor(eng)))
        except Exception as e:
            read_why.configure(text=f"read unavailable: {e.__class__.__name__}")
        macro_lbl.configure(text="   ".join(f"{n} {pct(qs[s]):+.1f}%" for n, s in MACRO_PRICES.items() if s in qs))
        try:
            econ_calendar.paint(econ_head, econ_body, econ_calendar.get(eng.core).banner("us"))
        except Exception:
            pass
        rank = sector_ranking(qs)
        biggest = max([abs(p) for _, p in rank] + [1.0])
        for (a, c, b), (name, p) in zip(rows, rank):
            a.configure(text=name)
            b.configure(text=f"{p:+.1f}%", fg=color(p))
            c.delete("all")
            c.create_line(75, 0, 75, 10, fill=LINE)
            w = int(abs(p) / biggest * 74)
            c.create_rectangle(75, 2, 75 + w, 8, fill=GOLD, width=0) if p > 0 else c.create_rectangle(75 - w, 2, 75, 8, fill=DOWN, width=0)
        items = eng.earnings.items
        earn_lbl.configure(text="\n".join(earnings_line(e) for e in items[:5]) if items else
                           ("None of the big names report in the next 2 weeks" if eng.earnings.status == "ok"
                            else eng.earnings.status))
        status.configure(text=eng.status + "   ·   prices: Yahoo (may be delayed)"
                         + (f"\niPhone: {phone_url}" if phone_url else ""))
        root.after(1000, tick)

    def sync_loop():
        """Every 5 min: pull newer files from GitHub (same as the iPhone) and restart if any changed."""
        def work():
            try:
                import auto_sync
                if auto_sync.check():
                    root.after(0, restart_self)
            except Exception as e:
                print("[sync] check failed:", e.__class__.__name__)
        threading.Thread(target=work, daemon=True).start()
        root.after(300000, sync_loop)

    tick()
    root.after(60000, sync_loop)
    root.mainloop()


def restart_self():
    import os, subprocess
    subprocess.Popen([sys.executable, str(HERE / "intel_bot.py"), "--after-update"], cwd=str(HERE))
    os._exit(0)

# ================= setup / test =================
def load_config():
    cfg = configparser.ConfigParser()
    if not cfg.read(CONFIG_FILE, encoding="utf-8"):
        sys.exit("config.ini not found next to intel_bot.py")
    for sec in ("telegram", "ai", "sec", "settings"):
        if sec not in cfg:
            cfg[sec] = {}
    syms = [x.strip().upper() for x in cfg["settings"].get("big_names", "").split(",") if x.strip()]
    if syms:
        known = {v: k for k, v in TOP10.items()}
        TOP10.clear()
        TOP10.update({known.get(s, s): s for s in syms})
        RX["company"] = _rx(COMPANY_WORDS + [s.lower() for s in syms] + [n.lower() for n in TOP10])
    return cfg

def run_test(cfg):
    eng = Engine(cfg)
    print("Checking sources...\n")
    ok = 0
    for f in eng.feeds():
        try:
            n = len(eng.fetch(f))
            ok += 1
            print(f"  OK    {f[0]:<16} {n} items")
        except Exception as e:
            print(f"  FAIL  {f[0]:<16} {e.__class__.__name__}")
    qs = eng.prices.refresh(force=True)
    print(f"\nSources working: {ok}/{len(eng.feeds())}")
    print(f"Prices: {index_line(qs)}  ({len(qs)} symbols loaded)")
    items = eng.earnings.refresh(force=True)
    print(f"Earnings calendar: {eng.earnings.status}")
    for e in items:
        print("   ", earnings_line(e))
    try:
        import oil_intel
        oe = oil_intel.OilEngine(sys.modules[__name__], cfg)
        print("\nOil sources:")
        for f in oil_intel.OIL_FEEDS:
            try:
                print(f"  OK    {f[0]:<16} {len(parse_feed(http_get(f[1]), f[0]))} items")
            except Exception as e:
                print(f"  FAIL  {f[0]:<16} {e.__class__.__name__}")
        oe.refresh_prices()
        print("Oil prices:", oe.oil_line(), f"({len(oe.prices())} symbols)")
        for k, v in oe.plumbing():
            print(f"   {k}: {v}")
    except Exception as e:
        print("Oil panel check failed:", e)
    try:
        import metals_intel
        me = metals_intel.MetalsEngine(sys.modules[__name__], cfg)
        print("\nMetals sources:")
        for f in metals_intel.METAL_FEEDS:
            try:
                print(f"  OK    {f[0]:<16} {len(parse_feed(http_get(f[1]), f[0]))} items")
            except Exception as e:
                print(f"  FAIL  {f[0]:<16} {e.__class__.__name__}")
        me.refresh_prices()
        print("Metals prices:", me.metal_line(), f"({len(me.prices())} symbols)")
        for k, v in me.plumbing():
            print(f"   {k}: {v}")
    except Exception as e:
        print("Metals panel check failed:", e)
    toast("US Market Intel test", f"Sources {ok}/{len(eng.feeds())} · {index_line(qs)}")
    print("\nA test pop-up should appear bottom-right of your screen.")
    if cfg["telegram"].get("bot_token", "").strip():
        print("Telegram:", "sent" if tg_send(cfg, "US Market Intel test message") else "failed")

if __name__ == "__main__":
    if sys.stdout is None:                     # started without a console window (pythonw)
        sys.stdout = sys.stderr = open(HERE / "log.txt", "a", encoding="utf-8", buffering=1)
    if "--after-update" in sys.argv:
        time.sleep(4)                          # let the old windows close first
    else:
        try:
            import auto_sync
            if auto_sync.check():              # newer files on GitHub - start again with them
                restart_self()
        except Exception as e:
            print("[sync] check failed:", e.__class__.__name__)
    config = load_config()
    if "--test" in sys.argv:
        run_test(config)
    else:
        run_window(config)
