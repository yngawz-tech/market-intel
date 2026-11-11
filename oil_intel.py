"""
Oil Market Intel - the oil (WTI crude) side panel for US Market Intel.
Opened with the OIL button on the right of the main window.
Uses the same helpers as intel_bot.py (passed in as `core`).
"""
import json, re, threading, time
from datetime import datetime, timedelta, date
from pathlib import Path

HERE = Path(__file__).parent
STATE_FILE = HERE / "state_oil.json"

# ================= what we watch =================
OIL_MAIN = {"WTI crude": "CL=F", "Brent crude": "BZ=F", "Gasoline": "RB=F", "Diesel": "HO=F"}
OIL_OTHER = {"Natural gas": "NG=F", "US dollar": "DX-Y.NYB", "Energy stocks": "XLE",
             "Oil drillers": "XOP", "Oil services": "OIH"}
TANKERS = {"Tanker freight (BWET)": "BWET", "Frontline (tankers)": "FRO", "DHT (tankers)": "DHT",
           "Intl Seaways (tankers)": "INSW"}
MAIN_LEVELS, OTHER_LEVELS, TANKER_LEVELS = [1.5, 3.0, 5.0], [3.0, 5.0, 8.0], [4.0, 7.0, 10.0]

MONTH_CODES = "FGHJKMNQUVXZ"

def _rx(words):
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)

OIL_RX = _rx(["oil", "crude", "wti", "brent", "opec", "opec+", "barrel", "barrels", "bpd", "refinery",
              "refineries", "refining", "gasoline", "diesel", "distillate", "distillates", "jet fuel",
              "inventories", "stockpiles", "cushing", "shale", "rig count", "rigs", "tanker", "tankers",
              "hormuz", "red sea", "houthi", "houthis", "pipeline", "aramco", "petroleum", "spr",
              "strategic petroleum reserve", "oilfield", "fuel"])
SUPPLY_RX = _rx(["saudi", "russia", "russian", "iran", "iranian", "iraq", "venezuela", "libya", "nigeria",
                 "kazakhstan", "uae", "kuwait", "sanctions", "embargo", "price cap", "ceasefire", "war",
                 "attack", "strike", "drone", "missile", "china", "demand", "recession", "fed", "tariff",
                 "tariffs", "hurricane", "storm"])
HEAVY_RX = _rx(["cut", "cuts", "hike", "hikes", "surprise", "unexpected", "unexpectedly", "draw", "build",
                "attack", "attacked", "seized", "seize", "outage", "halt", "halted", "shut", "shutdown",
                "explosion", "fire", "blast", "disruption", "disrupted", "blockade", "boost", "boosts",
                "record", "plunge", "plunges", "surge", "surges", "soar", "soars", "tumble", "tumbles",
                "jump", "jumps", "slump", "slumps", "crash", "emergency", "breaking", "agreement", "deal",
                "sharply", "drop", "drops", "fall", "falls", "slide", "slides", "rally", "rallies", "spike",
                "ceasefire", "peace", "talks", "sanctions", "threat", "threatens", "warns"])
NOISE_RX = _rx(["stocks to buy", "stock to buy", "dividend", "dividends", "best", "should you", "buy now",
                "motley", "millionaire", "retire", "retirement", "my portfolio", "price prediction",
                "forecast 2027", "forecast 2030", "etf review", "podcast", "webinar"])
UP_RX = _rx(["surge", "surges", "jump", "jumps", "rise", "rises", "rising", "rally", "rallies", "soar", "soars",
             "climb", "climbs", "gain", "gains", "higher", "spike", "spikes", "attack", "attacked", "strike",
             "seized", "outage", "disruption", "disrupted", "blockade", "sanctions", "draw", "draws",
             "cut", "cuts", "shortage", "tight", "tighter", "escalate", "escalates", "escalation"])
DOWN_RX = _rx(["fall", "falls", "falling", "drop", "drops", "slide", "slides", "slump", "slumps", "sink", "sinks",
               "tumble", "tumbles", "plunge", "plunges", "lower", "decline", "declines", "loss", "losses",
               "peace", "ceasefire", "truce", "talks", "deal", "build", "builds", "glut", "surplus",
               "oversupply", "boost output", "raise output", "increase output", "weak demand", "slowdown"])
OFFICIAL_BIG = _rx(["weekly petroleum status", "petroleum status report", "short-term energy outlook",
                    "crude oil inventories", "this week in petroleum"])

def g_news(q):
    import urllib.parse
    return ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:1h")
            + "&hl=en-US&gl=US&ceid=US:en")

# name, url, check every N seconds, official?
OIL_FEEDS = [
    ("EIA", "https://www.eia.gov/rss/press_rss.xml", 10, True),
    ("EIA today", "https://www.eia.gov/rss/todayinenergy.xml", 300, False),
    ("OilPrice", "https://oilprice.com/rss/main", 15, False),
    ("investingLive", "https://investinglive.com/feed", 3, False),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_bulletins", 4, False),
    ("CNBC energy", "https://www.cnbc.com/id/19836768/device/rss/rss.html", 10, False),
    ("News", g_news("\"oil prices\" OR \"crude oil\" OR \"crude futures\" OR brent OR wti"), 30, False),
    ("News", g_news("opec OR \"opec+\" OR \"saudi aramco\" OR \"oil output\""), 30, False),
    ("News", g_news("\"crude inventories\" OR \"crude stocks\" OR EIA OR \"API crude\" OR cushing"), 45, False),
    ("News", g_news("hormuz OR \"red sea\" OR houthi OR tanker OR \"oil tanker\" OR \"shadow fleet\""), 30, False),
    ("News", g_news("iran oil OR russia oil OR \"oil sanctions\" OR venezuela oil"), 30, False),
    ("News", g_news("refinery outage OR refinery fire OR pipeline oil OR \"strategic petroleum reserve\""), 45, False),
]

# ================= oil plumbing (the Essex Boys part) =================
FIXED_HOLIDAYS = {(1, 1), (6, 19), (7, 4), (12, 25)}

def is_business(d):
    return d.weekday() < 5 and (d.month, d.day) not in FIXED_HOLIDAYS

def business_days_back(d, n):
    while n > 0:
        d -= timedelta(days=1)
        if is_business(d):
            n -= 1
    return d

def cl_last_trade(contract_year, contract_month):
    """CL stops trading 3 business days before the 25th of the month BEFORE delivery
    (if the 25th is a weekend, count from the business day before it). Holidays ignored."""
    m, y = contract_month - 1, contract_year
    if m == 0:
        m, y = 12, y - 1
    d25 = date(y, m, 25)
    start = d25
    if not is_business(d25):
        start = business_days_back(d25, 1)
    return business_days_back(start, 3)

def cl_contracts(today):
    """Front and next WTI contract (year, month), using the expiry rule above."""
    y, m = today.year, today.month + 1
    out = []
    while len(out) < 2:
        if m > 12:
            m, y = 1, y + 1
        if cl_last_trade(y, m) >= today:
            out.append((y, m))
        m += 1
    return out

def cl_symbol(y, m):
    return f"CL{MONTH_CODES[m - 1]}{str(y)[-2:]}.NYM"

def month_name(y, m):
    return date(y, m, 1).strftime("%b")

def next_weekday_time(now, weekday, hm):
    """Next time (NY) at weekday (0=Mon) and 'HH:MM'."""
    h, mi = map(int, hm.split(":"))
    d = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    days = (weekday - now.weekday()) % 7
    d += timedelta(days=days)
    if d < now:
        d += timedelta(days=7)
    return d

OIL_EVENTS = [  # weekday, NY time, name, simple words
    (1, "16:30", "API stockpiles", "early estimate of US oil in storage"),
    (2, "10:30", "EIA stockpiles", "official US oil in storage - big mover"),
    (4, "13:00", "Baker Hughes rig count", "US oil wells being drilled"),
    (4, "15:30", "CFTC positioning", "how big funds are betting on oil"),
]

# ================= engine =================
class OilEngine:
    def __init__(self, core, cfg):
        self.c, self.cfg, self.history, self.seq = core, cfg, [], 0
        try:
            self.st = json.loads(STATE_FILE.read_text())
        except Exception:
            self.st = {}
        for k, v in (("seen", []), ("recent", []), ("alarms", []), ("top", []), ("events", []),
                     ("seeded", False), ("hist", []), ("tone", [])):
            self.st.setdefault(k, v)
        self.caught_up, self.ai_cache = False, {"t": 0, "read": None, "mentor": None}
        self.data, self.lock, self.last_poll = {}, threading.Lock(), {}
        today = self.c.ny_now().strftime("%Y-%m-%d")
        for a in self.st["hist"]:
            if a.get("day") == today:
                self.seq += 1
                self.history.append({**a, "id": self.seq})
        self.status, self.window_open = "Starting...", False
        self.contracts = []

    # ---- prices ----
    def refresh_prices(self):
        import concurrent.futures as cf
        if time.time() - getattr(self, "_pt", 0) < 15:      # prices every 15 s (news is checked every 2 s)
            return
        self._pt = time.time()
        self.contracts = cl_contracts(self.c.ny_now().date())
        syms = [*OIL_MAIN.values(), *OIL_OTHER.values(), *TANKERS.values(),
                *[cl_symbol(y, m) for y, m in self.contracts]]
        out = {}
        with cf.ThreadPoolExecutor(10) as ex:
            futs = {ex.submit(self.c.quote, s): s for s in syms}
            for f in cf.as_completed(futs):
                try:
                    out[futs[f]] = f.result()
                except Exception:
                    pass
        if out:
            with self.lock:
                self.data = out

    def prices(self):
        with self.lock:
            return dict(self.data)

    def oil_line(self, qs=None):
        qs = qs or self.prices()
        parts = [f"{n.split()[0]} {self.c.pct(qs[s]):+.1f}%" for n, s in list(OIL_MAIN.items())[:2] if s in qs]
        return " | ".join(parts) if parts else "prices unavailable"

    def plumbing(self, qs=None):
        """Simple-word facts about how the oil market is wired today."""
        qs = qs or self.prices()
        out = []
        if len(self.contracts) == 2:
            (y1, m1), (y2, m2) = self.contracts
            s1, s2 = cl_symbol(y1, m1), cl_symbol(y2, m2)
            if s1 in qs and s2 in qs:
                sp = qs[s1][0] - qs[s2][0]
                sp_prev = (qs[s1][1] or 0) - (qs[s2][1] or 0)
                state = ("oil is wanted NOW (tight supply)" if sp > 0.05 else
                         "too much oil now (loose supply)" if sp < -0.05 else "balanced")
                trend = "tighter" if sp > sp_prev + 0.02 else "looser" if sp < sp_prev - 0.02 else "same"
                out.append(("Now vs next month",
                            f"{month_name(y1, m1)} {sp:+.2f}$ vs {month_name(y2, m2)}  ·  {state}  ·  {trend} than yesterday"))
            last = cl_last_trade(*self.contracts[0])
            days = (last - self.c.ny_now().date()).days
            warn = "  ·  roll week - moves can be strange" if days <= 5 else ""
            out.append(("Contract", f"{month_name(*self.contracts[0])} WTI stops trading {last:%a %d %b} "
                                    f"({days} days){warn}"))
        if "BZ=F" in qs and "CL=F" in qs:
            gap = qs["BZ=F"][0] - qs["CL=F"][0]
            gap_prev = (qs["BZ=F"][1] or 0) - (qs["CL=F"][1] or 0)
            out.append(("Brent minus WTI",
                        f"{gap:+.2f}$  ·  {'world oil getting dearer vs US' if gap > gap_prev + 0.05 else 'US oil catching up' if gap < gap_prev - 0.05 else 'steady'}"))
        if all(s in qs for s in ("RB=F", "HO=F", "CL=F")):
            crack = lambda i: (2 * qs["RB=F"][i] * 42 + qs["HO=F"][i] * 42 - 3 * qs["CL=F"][i]) / 3
            try:
                now_c, prev_c = crack(0), crack(1)
                out.append(("Refinery profit",
                            f"{now_c:.1f}$ per barrel  ·  {'rising - refiners want more crude' if now_c > prev_c + 0.3 else 'falling - refiners need less crude' if now_c < prev_c - 0.3 else 'steady'}"))
            except Exception:
                pass
        return out

    def calendar(self):
        now = self.c.ny_now()
        items = []
        for wd, hm, name, words in OIL_EVENTS:
            t = next_weekday_time(now, wd, hm)
            items.append((t, f"{t:%a} {hm}  {name}", words))
        if self.contracts:
            last = cl_last_trade(*self.contracts[0])
            items.append((datetime(last.year, last.month, last.day, 14, 30, tzinfo=now.tzinfo),
                          f"{last:%a %d %b}  WTI {month_name(*self.contracts[0])} contract ends", "last day to trade it"))
        return sorted(items)[:5]

    # ---- opinions: my read + mentor's desk note ----
    def facts(self, qs=None):
        qs = qs or self.prices()
        f = {"wti": self.c.pct(qs["CL=F"]) if "CL=F" in qs else None}
        if len(self.contracts) == 2:
            s1, s2 = cl_symbol(*self.contracts[0]), cl_symbol(*self.contracts[1])
            if s1 in qs and s2 in qs:
                f["spread"] = qs[s1][0] - qs[s2][0]
                f["spread_chg"] = f["spread"] - ((qs[s1][1] or 0) - (qs[s2][1] or 0))
            f["days_to_end"] = (cl_last_trade(*self.contracts[0]) - self.c.ny_now().date()).days
        if all(k in qs for k in ("RB=F", "HO=F", "CL=F")):
            crack = lambda i: (2 * qs["RB=F"][i] * 42 + qs["HO=F"][i] * 42 - 3 * qs["CL=F"][i]) / 3
            f["crack_chg"] = crack(0) - crack(1)
        tk = [self.c.pct(qs[s]) for s in TANKERS.values() if s in qs]
        f["tankers"] = sum(tk) / len(tk) if tk else None
        tones = self.st["tone"]
        f["tone_up"] = sum(1 for t in tones if t["v"] > 0)
        f["tone_down"] = sum(1 for t in tones if t["v"] < 0)
        f["peace"] = any(re.search(r"\b(peace|ceasefire|truce|talks)\b", t["title"], re.I) for t in tones)
        f["war"] = any(re.search(r"\b(attack|strike|missile|drone|hormuz|seiz)", t["title"], re.I) for t in tones)
        f["opec"] = any(re.search(r"\bopec", t["title"], re.I) for t in tones)
        return f

    def my_read(self):
        import econ_calendar
        return econ_calendar.hi_conf(self.c, self.st, econ_calendar.adjust_read(self.c, self._my_read(), "oil"), "oil")

    def _my_read(self):
        """Rule-based read from today's data (or Claude's own words if an API key is set)."""
        if self.ai_cache["read"]:
            return self.ai_cache["read"]
        f = self.facts()
        score, why = 0, []
        w = f.get("wti")
        if w is not None and abs(w) >= 0.7:
            score += 1 if w > 0 else -1
            why.append(f"price is {'up' if w > 0 else 'down'} {abs(w):.1f}% today")
        sp = f.get("spread")
        if sp is not None:
            if sp > 0.05 and f.get("spread_chg", 0) >= 0:
                score += 1; why.append("next-month gap says oil is tight and getting tighter")
            elif sp < -0.05 or f.get("spread_chg", 0) < -0.05:
                score -= 1; why.append("next-month gap is getting looser (less urgent demand)")
        c = f.get("crack_chg")
        if c is not None and abs(c) >= 0.3:
            score += 1 if c > 0 else -1
            why.append(f"refiners' profit is {'rising' if c > 0 else 'falling'}")
        up, dn = f["tone_up"], f["tone_down"]
        if up + dn >= 3 and abs(up - dn) >= 2:
            score += 1 if up > dn else -1
            why.append(f"news mood: {up} push-up vs {dn} push-down headlines (3 hours)")
        if score >= 2:
            bias, conf = "UP", "medium" if score == 2 else "high"
        elif score <= -2:
            bias, conf = "DOWN", "medium" if score == -2 else "high"
        else:
            bias, conf = "NO CLEAR BIAS", "low"
        if f["peace"] and f["war"]:
            why.append("war and peace headlines are fighting - expect sharp swings both ways")
        return {"bias": bias, "conf": conf, "why": why[:5 if conf == "high" else 3] or ["signals are mixed or quiet"],
                "src": "rules from today's data"}

    def mentor(self):
        import econ_calendar
        note = econ_calendar.mentor_note(self.c, "oil")
        return econ_calendar.short_mentor(self.my_read(), "oil", ([note] if note else []) + self._mentor(), self.c, self.facts())

    def _mentor(self):
        """Veteran desk-style if-then notes from today's situation (context, not advice)."""
        if self.ai_cache["mentor"]:
            return self.ai_cache["mentor"]
        f, now, out = self.facts(), self.c.ny_now(), []
        if now.weekday() == 2 and now.strftime("%H:%M") < "11:00":
            out.append("EIA day: if the stockpile number is a big surprise, the first spike is often noise - "
                       "let the first 5-minute candle close before you trust the direction.")
        if now.weekday() == 1 and now.strftime("%H:%M") > "15:00":
            out.append("API tonight at 16:30: if it shows a big draw, Wednesday's EIA usually gets more attention.")
        if f.get("days_to_end") is not None and f["days_to_end"] <= 5:
            out.append("Roll week: big money is moving to next month's contract - if moves look strange, "
                       "check you are on the right contract in TradingView.")
        w, sp, chg = f.get("wti"), f.get("spread"), f.get("spread_chg", 0)
        if w is not None and sp is not None:
            if w < -0.7 and sp > 0.05:
                out.append("Price is falling but the next-month gap still says tight - if the drop stalls, "
                           "buyers often step back in.")
            elif w > 0.7 and chg < -0.03:
                out.append("Price is up but the next-month gap is loosening - if the headline behind the "
                           "rally fades, price can give it back.")
            elif w > 0.7 and chg >= 0:
                out.append("Price and the next-month gap are both firming - real buying, not just headlines.")
        if f["peace"]:
            out.append("Peace-talk headlines push oil down fast - if the talks are denied, it can snap back just as fast.")
        if f["war"]:
            out.append("Attack or Hormuz headlines spike oil - if no oil is actually lost, the spike often fades within hours.")
        if f["opec"]:
            out.append("OPEC in the news: if they surprise on output, the move can last days, not minutes.")
        if now.weekday() == 4 and now.strftime("%H:%M") >= "11:00":
            out.append("Friday afternoon: rig count at 13:00 and weekend risk - big players often cut positions before the close.")
        if not out:
            out.append("Quiet tape: if nothing new comes out, oil usually follows the dollar and stock markets today.")
        return out[:3]

    def refresh_ai(self):
        key = self.cfg["ai"].get("anthropic_api_key", "").strip() if "ai" in self.cfg else ""
        if not key or time.time() - self.ai_cache["t"] < 900:
            return
        self.ai_cache["t"] = time.time()
        f, plumb = self.facts(), self.plumbing()
        heads = "\n".join(h["title"] for h in self.history[-12:])
        prompt = ("You are helping Villiam, new to oil, who trades WTI crude futures intraday (New York time). "
                  f"Data now: {json.dumps(f, default=str)}\nPlumbing: {plumb}\nRecent oil headlines:\n{heads}\n\n"
                  "Reply in JSON only: {\"bias\": \"UP|DOWN|NO CLEAR BIAS\", \"conf\": \"low|medium|high\", "
                  "\"why\": [max 3 very short simple reasons], \"mentor\": [max 3 if-then notes in the voice of a "
                  "calm veteran oil trader, kid-level words, context only, no entries, stops or advice]}")
        try:
            r = self.c.http_post_json("https://api.anthropic.com/v1/messages",
                                      {"model": self.cfg["ai"].get("model", "claude-haiku-5-5"), "max_tokens": 400,
                                       "messages": [{"role": "user", "content": prompt}]},
                                      {"x-api-key": key, "anthropic-version": "2023-06-01"})
            txt = r["content"][0]["text"]
            j = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
            self.ai_cache["read"] = {"bias": j["bias"], "conf": j["conf"], "why": j["why"][:3], "src": "Claude"}
            self.ai_cache["mentor"] = j["mentor"][:3]
        except Exception:
            pass

    def best_news(self, n=3):
        """The n most relevant oil items today: biggest impact first, newer wins ties."""
        now = time.time()
        def rank(a):
            age_h = (now - a.get("ts", now)) / 3600
            return a.get("imp", 0) + (2 if age_h < 1 else 1 if age_h < 3 else 0)
        news = [a for a in self.history if a["kind"] not in ("COMING UP",)]
        out = []
        for a in sorted(news, key=lambda a: (rank(a), a.get("ts", 0)), reverse=True):
            if not self.c.is_duplicate(a["title"], [sorted(self.c.words(b["title"])) for b in out]):
                out.append(a)
            if len(out) == n:
                break
        return out

    # ---- alerts ----
    def top_today(self):
        return self.c.pick_top(self.st["top"], self.cfg, 3)

    def emit(self, kind, title, lines, link="", imp=0, quiet=False):
        now = self.c.ny_now()
        stamp, day = now.strftime("%H:%M"), now.strftime("%Y-%m-%d")
        top = imp >= int(self.cfg["settings"].get("top_story_level", "7"))
        if top and any(t["title"] == title and t["day"] == day for t in self.st["top"]):
            top = False
        if top:
            self.st["top"] = [t for t in self.st["top"] if t["day"] == day]
            self.st["top"].append({"day": day, "time": stamp, "kind": kind, "title": title,
                                   "line": lines[0] if lines else "", "impact": imp})
            self.st["top"] = self.st["top"][-40:]
        self.seq += 1
        self.history.append({"id": self.seq, "time": stamp, "kind": kind, "title": title, "lines": lines,
                             "link": link, "top": top, "imp": imp, "ts": time.time()})
        self.history = self.history[-200:]
        self.st["hist"] = [h for h in self.st["hist"] if h.get("day") == day][-80:] + [
            {"day": day, "time": stamp, "kind": kind, "title": title, "lines": lines, "link": link, "top": top,
             "imp": imp, "ts": time.time()}]
        if quiet:
            return
        mode = self.cfg["settings"].get("oil_popups", "top").lower()
        if mode.startswith("y") or mode == "all" or (mode == "top" and top):
            self.c.toast(("OIL TOP STORY · " if top else "OIL · ") + f"{kind} · {stamp} NY",
                         title + ("\n" + lines[0] if lines else ""))
        print(f"[{stamp}] OIL {kind}: {title}")

    def impact(self, title, always, qs):
        if not OIL_RX.search(title):
            return 10 if always and OFFICIAL_BIG.search(title) else (3 if always else 0)
        s = 3 + (2 if SUPPLY_RX.search(title) else 0) + (2 if HEAVY_RX.search(title) else 0)
        if NOISE_RX.search(title):
            s -= 4
        if always and OFFICIAL_BIG.search(title):
            s += 5
        if self.c.sector_of(title):
            s += 4                       # a whole sector moving = market driver
        if "CL=F" in qs and abs(self.c.pct(qs["CL=F"])) >= 2:
            s += 2
        import econ_calendar
        return s + econ_calendar.boost(self.c, title, "oil")

    def check_news(self):
        import concurrent.futures as cf
        from datetime import timezone
        now = time.time()
        due = [f for f in OIL_FEEDS if now - self.last_poll.get(f[1], 0) >= f[2]]
        if not due:
            return
        results = []
        with cf.ThreadPoolExecutor(8) as ex:
            futs = {ex.submit(lambda f: self.c.parse_feed(self.c.http_get(f[1], None, 6), f[0]), f): f for f in due}
            for fut in cf.as_completed(futs):
                f = futs[fut]
                self.last_poll[f[1]] = now
                fails = self.__dict__.setdefault("_fails", {})
                try:
                    results.append((f, fut.result()))
                    fails.pop(f[1], None)
                except Exception:          # source refused or too slow: wait longer so we don't get blocked
                    fails[f[1]] = fails.get(f[1], 0) + 1
                    self.last_poll[f[1]] = now + min(300, f[2] * fails[f[1]])
        seen = set(self.st["seen"])
        utc_now = datetime.now(timezone.utc)
        max_age = timedelta(minutes=int(self.cfg["settings"].get("max_news_age_minutes", "45")))
        qs = self.prices()
        tone_keys = {t["k"] for t in self.st["tone"]}
        shown = {h["title"] for h in self.history}
        catch = []
        for (name, _, _, always), items in results:
            for it in items:
                key = it["id"] or it["title"]
                young = it["time"] and utc_now - it["time"] <= timedelta(hours=3)
                if young and key not in tone_keys and OIL_RX.search(it["title"]) and not NOISE_RX.search(it["title"]):
                    v = (1 if UP_RX.search(it["title"]) else 0) - (1 if DOWN_RX.search(it["title"]) else 0)
                    if v:
                        tone_keys.add(key)
                        self.st["tone"].append({"k": key, "t": it["time"].isoformat(), "v": v, "title": it["title"]})
                if not self.caught_up and young and utc_now - it["time"] <= timedelta(hours=2) \
                        and it["title"] not in shown and self.impact(it["title"], always, qs) >= 5:
                    catch.append((it["time"], name, always, it))
                if key in seen:
                    continue
                seen.add(key)
                self.st["seen"].append(key)
                if not self.st["seeded"]:
                    continue
                if it["time"] and utc_now - it["time"] > max_age:
                    continue
                imp = self.impact(it["title"], always, qs)
                if imp < 5 or self.c.is_duplicate(it["title"], self.st["recent"]) \
                        or self.c.is_duplicate(it["title"], [sorted(self.c.words(h["title"])) for h in self.history]):
                    continue
                self.st["recent"].append(sorted(self.c.words(it["title"])))
                age = f" · {max(0, int((utc_now - it['time']).total_seconds() // 60))} min old" if it["time"] else ""
                kind = "OFFICIAL" if always else ("SUPPLY / WORLD" if SUPPLY_RX.search(it["title"]) else "OIL NEWS")
                self.emit(kind, it["title"], [self.oil_line(qs), f"{it['source']}{age}"], it["link"], imp)
        if not self.caught_up:                      # show the last 2 hours of oil news once, without pop-ups
            done = []
            for t, name, always, it in sorted(catch, key=lambda x: x[0]):
                if self.c.is_duplicate(it["title"], done):
                    continue
                done.append(sorted(self.c.words(it["title"])))
                self.st["recent"].append(done[-1])
                mins = int((utc_now - t).total_seconds() // 60)
                self.emit("EARLIER (" + t.astimezone(self.c.ny_now().tzinfo).strftime("%H:%M") + " NY)",
                          it["title"], [f"{it['source']} · {mins} min ago"], it["link"],
                          imp=self.impact(it["title"], always, qs), quiet=True)
            self.caught_up = True
        cutoff = utc_now - timedelta(hours=3)
        self.st["tone"] = [t for t in self.st["tone"] if datetime.fromisoformat(t["t"]) >= cutoff][-150:]
        self.st["seeded"] = True

    def check_alarms(self):
        qs = self.prices()
        day = self.c.ny_now().strftime("%Y-%m-%d")
        for group, levels, label, big in ((OIL_MAIN, MAIN_LEVELS, "OIL MOVE", 3.0),
                                          (OIL_OTHER, OTHER_LEVELS, "LINKED MARKET", 5.0),
                                          (TANKERS, TANKER_LEVELS, "TANKER WATCH", 7.0)):
            for name, sym in group.items():
                if sym not in qs or sym == "DX-Y.NYB":
                    continue
                p = self.c.pct(qs[sym])
                hit = [lv for lv in levels if abs(p) >= lv]
                if not hit:
                    continue
                key = f"{day}:{sym}:{'up' if p > 0 else 'down'}:{max(hit)}"
                if key in self.st["alarms"]:
                    continue
                self.st["alarms"].append(key)
                self.emit(label, f"{name} {'UP' if p > 0 else 'DOWN'} {p:+.1f}% today", [self.oil_line(qs)],
                          imp=8 if max(hit) >= big else 0)

    def check_events(self):
        now = self.c.ny_now()
        if now.weekday() >= 5:
            return
        for wd, hm, name, words in OIL_EVENTS[:2]:
            t = next_weekday_time(now, wd, hm)
            mins = (t - now).total_seconds() / 60
            key = f"{t:%Y-%m-%d}:{name}"
            if 0 < mins <= 30 and key not in self.st["events"]:
                self.st["events"].append(key)
                self.emit("COMING UP", f"{name} at {hm} NY (in {int(mins)} min)",
                          [words, "Oil often jumps right after this number"])

    def save(self):
        for k, n in (("seen", 4000), ("recent", 200), ("alarms", 500), ("top", 20), ("events", 50), ("hist", 81)):
            self.st[k] = self.st[k][-n:]
        try:
            STATE_FILE.write_text(json.dumps(self.st))
        except Exception:
            pass

    def run_forever(self):
        secs = int(self.cfg["settings"].get("check_seconds", "15"))
        while True:
            try:
                self.refresh_prices()
                if self.c.in_session(self.cfg):
                    self.check_news()
                    self.check_alarms()
                    self.check_events()
                    self.refresh_ai()
                    if time.time() - getattr(self, "_saved", 0) > 15:     # write the file every 15 s, not every check
                        self.save()
                        self._saved = time.time()
                    self.status = f"Live · last check {self.c.ny_now():%H:%M:%S} NY"
                else:
                    self.status = f"Waiting · alerts start {self.cfg['settings']['start_ny']} NY (Mon-Fri)"
                time.sleep(max(secs, 2))
            except Exception as e:
                self.status = f"Error, retrying: {e.__class__.__name__}"
                time.sleep(30)

# ================= window =================

def _econ_box(tk, parent, F, GOLD):
    box = tk.Frame(parent, bg="#221D10", highlightbackground=GOLD, highlightthickness=1)
    box.pack(fill="x", padx=14, pady=(6, 0))
    import econ_calendar
    head = econ_calendar.make_head(tk, box, F, GOLD)
    head.pack(fill="x", padx=8, pady=(3, 0))
    body = tk.Label(box, text="", bg="#221D10", fg="#AAAAAA", font=(F, 8), anchor="w", justify="left", wraplength=430)
    body.pack(fill="x", padx=8, pady=(0, 4))
    return head, body

def open_oil_window(tk, root, eng, style, dark_titlebar, webbrowser, others=(), place=None):
    """Compact oil panel, same size as the US Market Intel window, docked on its right."""
    GOLD, TEXT, GREY, LINE, BG, DOWN = style
    F, SOFT = "Segoe UI", "#AAAAAA"
    color = lambda p: GOLD if p > 0 else DOWN
    win = tk.Toplevel(root)
    win.title("Oil Market Intel")
    win.configure(bg=BG)
    win.attributes("-topmost", bool(root.attributes("-topmost")))
    dark_titlebar(win)

    def dock():                                   # same size and same place as US Market Intel
        root.update_idletasks()
        win.geometry(place() if place else root.geometry())
    win.after(50, dock)
    win.after(400, dock)

    def label(text, color_=GREY, size=9, bold=False, pady=(8, 1)):
        tk.Label(win, text=text, bg=BG, fg=color_, font=(F, size, "bold" if bold else "normal"),
                 anchor="w").pack(fill="x", padx=17, pady=pady)

    # status first so it always stays visible at the bottom
    status = tk.Label(win, text="", bg=BG, fg=GREY, font=(F, 8), anchor="w")
    status.pack(side="bottom", fill="x", padx=17, pady=(2, 6))

    top = tk.Frame(win, bg=BG)
    top.pack(fill="x", padx=14, pady=(10, 2))
    tiles = {}
    for name in OIL_MAIN:
        f = tk.Frame(top, bg=BG, highlightbackground=LINE, highlightthickness=1)
        f.pack(side="left", expand=True, fill="x", padx=2)
        tk.Label(f, text=name.replace(" crude", ""), bg=BG, fg=GREY, font=(F, 8)).pack(pady=(3, 0))
        tiles[name] = tk.Label(f, text="--", bg=BG, fg=TEXT, font=(F, 11, "bold"))
        tiles[name].pack()
        tiles[name + "$"] = tk.Label(f, text="", bg=BG, fg=GREY, font=(F, 7))
        tiles[name + "$"].pack(pady=(0, 3))
    other_lbl = tk.Label(win, text="", bg=BG, fg=GREY, font=(F, 7), anchor="w", justify="left", wraplength=430)
    other_lbl.pack(fill="x", padx=17)
    import econ_calendar
    econ_head, econ_body = _econ_box(tk, win, F, GOLD)

    drivers_row = tk.Label(win, text="Top oil drivers today", bg=BG, fg=GOLD, font=(F, 10, "bold"), anchor="w")
    drivers_row.pack(fill="x", padx=17, pady=(8, 2))
    pin = tk.Text(win, bg="#221D10", fg=TEXT, font=(F, 9), wrap="word", relief="flat", height=2, width=10,
                  padx=6, pady=3, highlightbackground=GOLD, highlightthickness=1)
    pin.pack(fill="x", padx=14)
    pin.tag_configure("t", font=(F, 9, "bold"))
    pin.tag_configure("s", foreground="#9A9A9A", font=(F, 8))
    pin.configure(state="disabled")

    def box(title):
        b = tk.Frame(win, bg=BG, highlightbackground=LINE, highlightthickness=1)
        b.pack(fill="x", padx=14, pady=(6, 0))
        head = tk.Label(b, text=title, bg=BG, fg=TEXT, font=(F, 9, "bold"), anchor="w")
        head.pack(fill="x", padx=8, pady=(3, 0))
        body = tk.Label(b, text="", bg=BG, fg=SOFT, font=(F, 8), anchor="w", justify="left", wraplength=420)
        body.pack(fill="x", padx=8, pady=(0, 4))
        return head, body
    read_head, read_why = box("My read")
    read_head, read_tm = econ_calendar.read_row(tk, F, read_head, read_why)
    _, ment_lbl = box("Mentor's desk note (if this, then that)")


    label("Oil plumbing (how the market is wired today)")
    plumb = tk.Frame(win, bg=BG)
    plumb.pack(fill="x", padx=17)
    plumb_rows = []
    for _ in range(4):
        r = tk.Frame(plumb, bg=BG)
        r.pack(fill="x")
        a = tk.Label(r, text="", bg=BG, fg=TEXT, font=(F, 8, "bold"), anchor="nw", width=17)
        a.pack(side="left", anchor="n")
        b = tk.Label(r, text="", bg=BG, fg=SOFT, font=(F, 8), anchor="w", justify="left", wraplength=310)
        b.pack(side="left", fill="x")
        plumb_rows.append((a, b))

    label("Tanker watch")
    tank = tk.Frame(win, bg=BG)
    tank.pack(fill="x", padx=17)
    tank_rows = {}
    for name in TANKERS:
        r = tk.Frame(tank, bg=BG)
        r.pack(fill="x")
        tk.Label(r, text=name, bg=BG, fg=TEXT, font=(F, 8), anchor="w", width=24).pack(side="left")
        c = tk.Canvas(r, width=130, height=9, bg=BG, highlightthickness=0)
        c.pack(side="left", padx=4)
        b = tk.Label(r, text="", bg=BG, fg=TEXT, font=(F, 8, "bold"), anchor="e", width=7)
        b.pack(side="right")
        tank_rows[name] = (c, b)

    label("Oil calendar (New York time)")
    cal_lbl = tk.Label(win, text="", bg=BG, fg=TEXT, font=(F, 8), anchor="w", justify="left", wraplength=430)
    cal_lbl.pack(fill="x", padx=17)

    label(f"Big oil earnings (next {eng.c.EARN_WEEKS} weeks)")
    earn_lbl = tk.Label(win, text="loading...", bg=BG, fg=TEXT, font=(F, 8), anchor="w", justify="left", wraplength=430)
    earn_lbl.pack(fill="x", padx=17)

    label("Most relevant oil news (top 3)")
    news = tk.Text(win, bg=BG, fg=TEXT, font=(F, 8), wrap="word", relief="flat", padx=4, pady=2,
                   height=6, width=10, highlightbackground=LINE, highlightthickness=1, cursor="arrow")
    news.pack(fill="both", expand=True, padx=14, pady=(0, 2))
    news.tag_configure("meta", font=(F, 7), foreground=GREY)
    news.tag_configure("title", font=(F, 8, "bold"))
    news.configure(state="disabled")

    state = {"sig": None, "news": None, "alive": True}

    def tick():
        if not state["alive"]:
            return
        items = sorted(eng.top_today(), key=lambda t: t["time"], reverse=True)[:3]
        sig = json.dumps(items)
        if sig != state["sig"]:
            state["sig"] = sig
            pin.configure(state="normal", height=max(1, sum(1 + (len(t["title"]) + 10) // 58 for t in items)))
            pin.delete("1.0", "end")
            if not items:
                pin.insert("end", "Nothing big yet. Supply shocks, OPEC, stockpile surprises and big moves appear here.", "s")
            for t in items:
                pin.insert("end", f"{t['time']}  {t['title']}\n", "t")
            pin.configure(state="disabled")
        qs = eng.prices()
        pct = eng.c.pct
        for name, sym in OIL_MAIN.items():
            if sym in qs:
                p = pct(qs[sym])
                tiles[name].configure(text=f"{'▲' if p > 0 else '▼'} {p:+.2f}%", fg=color(p))
                tiles[name + "$"].configure(text=f"{qs[sym][0]:.2f}$")
        other_lbl.configure(text="   ".join(f"{n} {pct(qs[s]):+.1f}%" for n, s in OIL_OTHER.items() if s in qs))
        try:
            econ_calendar.paint(econ_head, econ_body, econ_calendar.get(eng.c).banner("oil"))
        except Exception:
            pass
        r = eng.my_read()
        head_txt, tm_txt = econ_calendar.hi_title(r)
        read_tm.configure(text=tm_txt)
        read_head.configure(text=head_txt, font=(F, 11 if r.get("hi_since") else 9, "bold"),
                            fg=GOLD if r["bias"] == "UP" else DOWN if r["bias"] == "DOWN" else TEXT)
        read_why.configure(text="\n".join("• " + w for w in r["why"]) )
        ment_lbl.configure(text="\n".join("• " + m for m in eng.mentor()))
        rows = eng.plumbing(qs)
        for i, (a, b) in enumerate(plumb_rows):
            a.configure(text=rows[i][0] if i < len(rows) else "")
            b.configure(text=rows[i][1] if i < len(rows) else "")
        tp = {n: pct(qs[s]) for n, s in TANKERS.items() if s in qs}
        biggest = max([abs(v) for v in tp.values()] + [2.0])
        for name, (c, b) in tank_rows.items():
            c.delete("all")
            c.create_line(65, 0, 65, 9, fill=LINE)
            if name in tp:
                p = tp[name]
                b.configure(text=f"{p:+.1f}%", fg=color(p))
                w = int(abs(p) / biggest * 64)
                c.create_rectangle(65, 2, 65 + w, 7, fill=GOLD, width=0) if p > 0 else c.create_rectangle(65 - w, 2, 65, 7, fill=DOWN, width=0)
            else:
                b.configure(text="--", fg=GREY)
        cal_lbl.configure(text="\n".join(f"{t}  ·  {w}" for _, t, w in eng.calendar()[:4]))
        if eng.c.Earnings.shared:
            ls, msg = eng.c.Earnings.shared.lines("oil")
            earn_lbl.configure(text="\n".join(ls) or msg)
        best = eng.best_news(3)
        nsig = json.dumps([a["id"] for a in best])
        if nsig != state["news"]:
            state["news"] = nsig
            news.configure(state="normal")
            news.delete("1.0", "end")
            if not best:
                news.insert("end", "No important oil news yet today.", "meta")
            for i, a in enumerate(best):
                src = a["lines"][-1] if a.get("lines") else ""
                news.insert("end", f"{a['time']} NY · {a['kind']} · {src}\n", "meta")
                tag = f"n{a['id']}"
                news.insert("end", a["title"] + ("\n\n" if i < len(best) - 1 else ""), ("title", tag))
                if a.get("link"):
                    news.tag_configure(tag, underline=False)
                    news.tag_bind(tag, "<Button-1>", lambda e, u=a["link"]: webbrowser.open(u))
                    news.tag_bind(tag, "<Enter>", lambda e: news.configure(cursor="hand2"))
                    news.tag_bind(tag, "<Leave>", lambda e: news.configure(cursor="arrow"))
            news.configure(state="disabled")
        status.configure(text=eng.status + "   ·   click a headline to open it")
        win.after(1000, tick)

    tk.Button(win, text="◂  US", command=lambda: on_close(), bg="#221D10", fg=GOLD, activebackground=GOLD,
              activeforeground="#161616", relief="flat", bd=0, font=(F, 9, "bold"), padx=10, pady=2,
              cursor="hand2", highlightbackground=GOLD, highlightthickness=1
              ).place(in_=drivers_row, relx=1.0, rely=0.5, anchor="e")
    for i, (name, go) in enumerate(others):          # switch straight to the other side panel
        tk.Button(win, text=f"{name}  \u25b8", command=go, bg="#221D10", fg=GOLD, activebackground=GOLD,
                  activeforeground="#161616", relief="flat", bd=0, font=(F, 9, "bold"), padx=10, pady=2,
                  cursor="hand2", highlightbackground=GOLD, highlightthickness=1
                  ).place(in_=drivers_row, relx=1.0, x=-72 - 92 * i, rely=0.5, anchor="e")

    def on_close():
        state["alive"] = False
        eng.window_open = False
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_close)
    eng.window_open = True
    tick()
    return win
