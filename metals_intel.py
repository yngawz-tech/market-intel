"""
Metals Market Intel - the gold and silver side panel for US Market Intel.
Opened with the METALS button on the main window. Same layout as the oil panel.
Uses the same helpers as intel_bot.py (passed in as `core`).
"""
import json, re, threading, time
from datetime import datetime, timedelta, date
from pathlib import Path

HERE = Path(__file__).parent
STATE_FILE = HERE / "state_metals.json"

# ================= what we watch =================
METAL_MAIN = {"Gold": "GC=F", "Silver": "SI=F", "Platinum": "PL=F", "Copper": "HG=F"}
METAL_OTHER = {"US dollar": "DX-Y.NYB", "10-year yield": "^TNX", "Inflation bonds (TIP)": "TIP", "VIX": "^VIX"}
METAL_STOCKS = {"Gold fund (GLD)": "GLD", "Silver fund (SLV)": "SLV", "Gold miners (GDX)": "GDX",
                "Junior gold miners (GDXJ)": "GDXJ", "Silver miners (SIL)": "SIL",
                "Newmont": "NEM", "Agnico Eagle": "AEM"}
MAIN_LEVELS, STOCK_LEVELS = [1.0, 2.0, 3.0], [3.0, 5.0, 8.0]
GOLD_MONTHS, SILVER_MONTHS = [2, 4, 6, 8, 10, 12], [3, 5, 7, 9, 12]
# Fed decision days (14:00 NY). Add next year's dates when the Fed publishes them.
FOMC_DAYS = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
             "2026-10-28", "2026-12-09"]

def _rx(words):
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)

METAL_RX = _rx(["gold", "silver", "bullion", "precious metal", "precious metals", "xau", "xag", "comex",
                "spot gold", "spot silver", "platinum", "palladium", "gold miners", "silver miners", "newmont",
                "barrick", "agnico", "safe haven", "safe-haven", "gold etf", "gold reserves", "ounce", "ounces"])
DRIVER_RX = _rx(["fed", "federal reserve", "fomc", "powell", "warsh", "rate cut", "rate cuts", "rate hike",
                 "interest rate", "rates", "inflation", "cpi", "ppi", "pce", "payrolls", "jobs report",
                 "jobless claims", "dollar", "yields", "treasury", "real yields", "central bank", "central banks",
                 "pboc", "china", "india", "war", "iran", "russia", "israel", "attack", "sanctions", "tariff",
                 "tariffs", "recession", "debt", "deficit", "geopolitical"])
HEAVY_RX = _rx(["record", "record high", "all-time high", "surprise", "unexpected", "unexpectedly", "attack",
                "plunge", "plunges", "surge", "surges", "soar", "soars", "tumble", "tumbles", "jump", "jumps",
                "slump", "slumps", "crash", "emergency", "breaking", "sharply", "rally", "rallies", "spike",
                "hawkish", "dovish", "rate cut", "rate hike", "ceasefire", "peace", "sanctions", "threat",
                "threatens", "warns", "buying spree", "selloff", "sell-off"])
NOISE_RX = _rx(["stocks to buy", "stock to buy", "dividend", "dividends", "best", "should you", "buy now",
                "motley", "millionaire", "retire", "retirement", "my portfolio", "price prediction",
                "forecast 2027", "forecast 2030", "etf review", "podcast", "webinar", "jewelry", "jewellery",
                "gold medal", "silver medal", "coin", "coins"])
UP_RX = _rx(["surge", "surges", "jump", "jumps", "rise", "rises", "rising", "rally", "rallies", "soar", "soars",
             "climb", "climbs", "gain", "gains", "higher", "record", "record high", "all-time high", "safe haven",
             "safe-haven", "rate cut", "rate cuts", "dovish", "weaker dollar", "dollar falls", "dollar slides",
             "attack", "escalation", "escalates", "war", "buying"])
DOWN_RX = _rx(["fall", "falls", "falling", "drop", "drops", "slide", "slides", "slump", "slumps", "sink", "sinks",
               "tumble", "tumbles", "plunge", "plunges", "lower", "decline", "declines", "loss", "losses",
               "selloff", "sell-off", "profit-taking", "hawkish", "rate hike", "stronger dollar", "dollar rises",
               "dollar jumps", "yields rise", "yields jump", "ceasefire", "peace", "truce"])
OFFICIAL_BIG = _rx(["fomc statement", "federal reserve issues", "monetary policy", "employment situation",
                    "consumer price index", "cpi", "producer price", "pce", "personal income", "gdp"])

def g_news(q):
    import urllib.parse
    return ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:1h")
            + "&hl=en-US&gl=US&ceid=US:en")

# name, url, check every N seconds, official?
METAL_FEEDS = [
    ("Fed", "https://www.federalreserve.gov/feeds/press_all.xml", 30, True),
    ("BLS", "https://www.bls.gov/feed/bls_latest.rss", 60, True),
    ("investingLive", "https://investinglive.com/feed", 20, False),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_bulletins", 20, False),
    ("CNBC markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html", 60, False),
    ("News", g_news("\"gold price\" OR \"gold prices\" OR \"spot gold\" OR \"gold futures\""), 45, False),
    ("News", g_news("\"silver price\" OR \"silver prices\" OR \"spot silver\" OR \"silver futures\""), 45, False),
    ("News", g_news("\"central bank\" gold OR \"gold reserves\" OR \"gold ETF\" OR bullion"), 60, False),
    ("News", g_news("\"safe haven\" gold OR \"real yields\" OR \"dollar index\" gold"), 60, False),
    ("News", g_news("platinum OR palladium OR \"copper prices\" OR \"gold miners\""), 90, False),
]

# ================= contracts and calendar =================
FIXED_HOLIDAYS = {(1, 1), (6, 19), (7, 4), (12, 25)}

def is_business(d):
    return d.weekday() < 5 and (d.month, d.day) not in FIXED_HOLIDAYS

def first_notice(y, m):
    """First notice day = last business day of the month before the contract month."""
    first = date(y, m, 1)
    d = first - timedelta(days=1)
    while not is_business(d):
        d -= timedelta(days=1)
    return d

def front_contract(today, months):
    """Next active contract (year, month) whose first notice day is still ahead (traders roll before it)."""
    for y in (today.year, today.year + 1):
        for m in months:
            if first_notice(y, m) >= today:
                return y, m
    return today.year + 1, months[0]

def month_name(y, m):
    return date(y, m, 1).strftime("%b")

def next_weekday_time(now, weekday, hm):
    h, mi = map(int, hm.split(":"))
    d = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    d += timedelta(days=(weekday - now.weekday()) % 7)
    if d < now:
        d += timedelta(days=7)
    return d

def first_friday(y, m):
    d = date(y, m, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7)

WEEKLY_EVENTS = [  # weekday, NY time, name, simple words
    (3, "08:30", "Jobless claims", "weekly US jobs number - moves the dollar and gold"),
    (4, "15:30", "CFTC positioning", "how big funds are betting on gold and silver"),
]

# ================= engine =================
class MetalsEngine:
    def __init__(self, core, cfg):
        self.c, self.cfg, self.history, self.seq = core, cfg, [], 0
        try:
            self.st = json.loads(STATE_FILE.read_text())
        except Exception:
            self.st = {}
        for k, v in (("seen", []), ("recent", []), ("alarms", []), ("top", []), ("events", []),
                     ("seeded", False), ("hist", []), ("tone", [])):
            self.st.setdefault(k, v)
        self.caught_up = False
        self.data, self.lock, self.last_poll = {}, threading.Lock(), {}
        today = self.c.ny_now().strftime("%Y-%m-%d")
        for a in self.st["hist"]:
            if a.get("day") == today:
                self.seq += 1
                self.history.append({**a, "id": self.seq})
        self.status, self.window_open = "Starting...", False

    # ---- prices ----
    def refresh_prices(self):
        import concurrent.futures as cf
        syms = [*METAL_MAIN.values(), *METAL_OTHER.values(), *METAL_STOCKS.values()]
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

    def metal_line(self, qs=None):
        qs = qs or self.prices()
        parts = [f"{n} {self.c.pct(qs[s]):+.1f}%" for n, s in list(METAL_MAIN.items())[:2] if s in qs]
        return " | ".join(parts) if parts else "prices unavailable"

    def facts(self, qs=None):
        qs = qs or self.prices()
        p = lambda s: self.c.pct(qs[s]) if s in qs else None
        f = {"gold": p("GC=F"), "silver": p("SI=F"), "dollar": p("DX-Y.NYB"), "yield": p("^TNX"),
             "vix": p("^VIX"), "gdx": p("GDX")}
        if "GC=F" in qs and "SI=F" in qs and qs["SI=F"][0] and qs["SI=F"][1]:
            f["ratio"] = qs["GC=F"][0] / qs["SI=F"][0]
            f["ratio_prev"] = (qs["GC=F"][1] or 0) / qs["SI=F"][1]
        f["miners_vs_gold"] = f["gdx"] - f["gold"] if f["gdx"] is not None and f["gold"] is not None else None
        today = self.c.ny_now().date()
        gy, gm = front_contract(today, GOLD_MONTHS)
        f["gold_contract"], f["gold_days"] = (gy, gm), (first_notice(gy, gm) - today).days
        tones = self.st["tone"]
        f["tone_up"] = sum(1 for t in tones if t["v"] > 0)
        f["tone_down"] = sum(1 for t in tones if t["v"] < 0)
        f["peace"] = any(re.search(r"\b(peace|ceasefire|truce|talks)\b", t["title"], re.I) for t in tones)
        f["war"] = any(re.search(r"\b(attack|strike|missile|drone|war|escalat)", t["title"], re.I) for t in tones)
        return f

    def plumbing(self, qs=None):
        """Simple-word facts about how the metals market is wired today."""
        f, out = self.facts(qs), []
        if "ratio" in f:
            r, rp = f["ratio"], f["ratio_prev"]
            out.append(("Gold / silver ratio", f"{r:.1f}  ·  " + ("silver gaining on gold" if r < rp - 0.3 else
                        "gold gaining on silver" if r > rp + 0.3 else "steady")))
        g, d = f["gold"], f["dollar"]
        if g is not None and d is not None:
            if g > 0.1 and d > 0.1:
                txt = "both up - fear buying"
            elif g < -0.1 and d < -0.1:
                txt = "both down - selling everything"
            elif abs(g) <= 0.1 or abs(d) <= 0.1:
                txt = "no clear link today"
            else:
                txt = "normal - moving opposite"
            out.append(("Gold vs dollar", f"gold {g:+.1f}%, dollar {d:+.1f}%  ·  {txt}"))
        m = f["miners_vs_gold"]
        if m is not None:
            out.append(("Miners vs gold", f"miners {f['gdx']:+.1f}% vs gold {g:+.1f}%  ·  " +
                        ("miners leading - confirms the move" if m > 1 else
                         "miners lagging - weak conviction" if m < -1 else "in line")))
        today = self.c.ny_now().date()
        for name, months in (("Gold", GOLD_MONTHS), ("Silver", SILVER_MONTHS)):
            y, mo = front_contract(today, months)
            fnd = first_notice(y, mo)
            days = (fnd - today).days
            warn = "  ·  roll week - moves can be strange" if days <= 5 else ""
            out.append((f"{name} contract", f"{month_name(y, mo)} - roll before {fnd:%a %d %b} ({days} days){warn}"))
        return out[:5]

    def calendar(self):
        now = self.c.ny_now()
        tz = now.tzinfo
        items = []
        for wd, hm, name, words in WEEKLY_EVENTS:
            t = next_weekday_time(now, wd, hm)
            items.append((t, f"{t:%a} {hm}  {name}", words))
        for d in FOMC_DAYS:
            t = datetime.fromisoformat(d).replace(hour=14, tzinfo=tz)
            if t >= now:
                items.append((t, f"{t:%a %d %b} 14:00  Fed decision", "biggest gold mover of the month"))
                break
        ff = first_friday(now.year, now.month)
        t = datetime(ff.year, ff.month, ff.day, 8, 30, tzinfo=tz)
        if t < now:
            nm = date(now.year + (now.month == 12), now.month % 12 + 1, 1)
            ff = first_friday(nm.year, nm.month)
            t = datetime(ff.year, ff.month, ff.day, 8, 30, tzinfo=tz)
        items.append((t, f"{t:%a %d %b} 08:30  Jobs report", "usually first Friday - big dollar and gold mover"))
        return sorted(items, key=lambda x: x[0])[:5]

    # ---- opinions: my read + mentor's desk note ----
    def my_read(self):
        import econ_calendar
        return econ_calendar.adjust_read(self.c, self._my_read(), "metals")

    def _my_read(self):
        f = self.facts()
        score, why = 0, []
        g, s = f["gold"], f["silver"]
        if g is None:
            return {"bias": "NO CLEAR BIAS", "conf": "low", "why": ["prices not loaded yet"], "lead": ""}
        if abs(g) >= 0.5:
            score += 1 if g > 0 else -1
            why.append(f"gold is {'up' if g > 0 else 'down'} {abs(g):.1f}% today")
        d = f["dollar"]
        if d is not None and abs(d) >= 0.3:
            score += -1 if d > 0 else 1
            why.append("dollar rising - pressure on gold" if d > 0 else "dollar falling - helps gold")
        y = f["yield"]
        if y is not None and abs(y) >= 1:
            score += -1 if y > 0 else 1
            why.append("10-year yield up - pressure on gold" if y > 0 else "10-year yield down - helps gold")
        m = f["miners_vs_gold"]
        if m is not None and abs(m) >= 1 and abs(g) >= 0.3:
            score += 1 if m > 0 else -1
            why.append("miners beating gold - real buying" if m > 0 else "miners lagging gold - weak conviction")
        if "ratio" in f and abs(f["ratio"] - f["ratio_prev"]) >= 0.5:
            up = f["ratio"] < f["ratio_prev"]
            score += 1 if up else -1
            why.append("silver beating gold - strong metals appetite" if up else "silver lagging gold - cautious mood")
        tu, td = f["tone_up"], f["tone_down"]
        if tu + td >= 3 and abs(tu - td) >= 2:
            score += 1 if tu > td else -1
            why.append(f"news mood: {tu} push-up vs {td} push-down headlines (3 hours)")
        if score >= 2:
            bias, conf = "UP", "medium" if score <= 3 else "high"
        elif score <= -2:
            bias, conf = "DOWN", "medium" if score >= -3 else "high"
        else:
            bias, conf = "NO CLEAR BIAS", "low"
        if f["peace"] and f["war"]:
            why.append("war and peace headlines are fighting - expect sharp swings both ways")
        lead = f"Gold {g:+.1f}%" + (f"  ·  Silver {s:+.1f}%" if s is not None else "")
        return {"bias": bias, "conf": conf, "why": why[:3] or ["signals are mixed or quiet"], "lead": lead}

    def mentor(self):
        import econ_calendar
        note = econ_calendar.mentor_note(self.c, "metals")
        out = self._mentor()
        return ([note] + out)[:3] if note else out

    def _mentor(self):
        """Veteran desk-style if-then notes (context, not advice)."""
        f, now, out = self.facts(), self.c.ny_now(), []
        hm, day = now.strftime("%H:%M"), now.strftime("%Y-%m-%d")
        g, s, d, y = f["gold"], f["silver"], f["dollar"], f["yield"]
        if day in FOMC_DAYS:
            out.append("Fed day: gold often sits still before 14:00 NY, then moves hard on the statement and the "
                       "14:30 press conference - the first spike often reverses.")
        if now.date() == first_friday(now.year, now.month) and hm < "10:00":
            out.append("Jobs report at 08:30: strong number = stronger dollar = gold down; weak number = gold up. "
                       "Let the first 5-minute candle close before you trust the direction.")
        if hm < "08:20":
            out.append("COMEX opens 08:20 NY: if gold holds the London move into the open it often carries on - "
                       "if it gives it back by 09:00, the London move was a trap.")
        if g is not None and d is not None and g > 0.3 and d > 0.2:
            out.append("Gold and the dollar rising together = fear buying - if the scary headline fades, gold can drop fast.")
        if g is not None and y is not None and g > 0.5 and y > 1:
            out.append("Gold up despite rising yields - strong demand; if yields keep climbing, the rally can tire.")
        if f["gold_days"] <= 5:
            out.append("Roll week: big money is moving to the next gold contract - check TradingView is on the right one.")
        if f["war"]:
            out.append("War headlines lift gold - if nothing actually escalates, the spike often fades within hours.")
        if f["peace"]:
            out.append("Peace headlines take the safe-haven bid out - gold can drop, then snap back if talks fail.")
        if g is not None and s is not None and abs(s) >= 1 and abs(s) >= 1.8 * abs(g):
            out.append("Silver is swinging about twice as hard as gold - normal; silver follows gold but moves bigger both ways.")
        if now.weekday() == 4 and hm >= "11:00":
            out.append("Friday afternoon: big players cut risk before the weekend - late moves can be sharp.")
        if not out:
            out.append("Quiet tape: if nothing new comes out, gold usually moves opposite the dollar and yields.")
        return out[:3]

    def best_news(self, n=3):
        now = time.time()
        def rank(a):
            age_h = (now - a.get("ts", now)) / 3600
            return a.get("imp", 0) + (2 if age_h < 1 else 1 if age_h < 3 else 0)
        news = [a for a in self.history if a["kind"] != "COMING UP"]
        out = []
        for a in sorted(news, key=lambda a: (rank(a), a.get("ts", 0)), reverse=True):
            if not self.c.is_duplicate(a["title"], [sorted(self.c.words(b["title"])) for b in out]):
                out.append(a)
            if len(out) == n:
                break
        return out

    # ---- alerts ----
    def top_today(self):
        day = self.c.ny_now().strftime("%Y-%m-%d")
        return [t for t in self.st["top"] if t["day"] == day]

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
            self.st["top"] = sorted(self.st["top"], key=lambda t: (-t["impact"], t["time"]))[:4]
        self.seq += 1
        self.history.append({"id": self.seq, "time": stamp, "kind": kind, "title": title, "lines": lines,
                             "link": link, "top": top, "imp": imp, "ts": time.time()})
        self.history = self.history[-200:]
        self.st["hist"] = [h for h in self.st["hist"] if h.get("day") == day][-80:] + [
            {"day": day, "time": stamp, "kind": kind, "title": title, "lines": lines, "link": link, "top": top,
             "imp": imp, "ts": time.time()}]
        if quiet:
            return
        mode = self.cfg["settings"].get("metals_popups", "top").lower()
        if mode.startswith("y") or mode == "all" or (mode == "top" and top):
            self.c.toast(("METALS TOP STORY · " if top else "METALS · ") + f"{kind} · {stamp} NY",
                         title + ("\n" + lines[0] if lines else ""))
        print(f"[{stamp}] METALS {kind}: {title}")

    def impact(self, title, always, qs):
        if not METAL_RX.search(title):
            return 10 if always and OFFICIAL_BIG.search(title) else (3 if always else 0)
        s = 3 + (2 if DRIVER_RX.search(title) else 0) + (2 if HEAVY_RX.search(title) else 0)
        if NOISE_RX.search(title):
            s -= 4
        if always and OFFICIAL_BIG.search(title):
            s += 5
        if "GC=F" in qs and abs(self.c.pct(qs["GC=F"])) >= 1.5:
            s += 2
        import econ_calendar
        return s + econ_calendar.boost(self.c, title, "metals")

    def check_news(self):
        import concurrent.futures as cf
        from datetime import timezone
        now = time.time()
        due = [f for f in METAL_FEEDS if now - self.last_poll.get(f[1], 0) >= f[2]]
        if not due:
            return
        results = []
        with cf.ThreadPoolExecutor(8) as ex:
            futs = {ex.submit(lambda f: self.c.parse_feed(self.c.http_get(f[1]), f[0]), f): f for f in due}
            for fut in cf.as_completed(futs):
                f = futs[fut]
                self.last_poll[f[1]] = now
                try:
                    results.append((f, fut.result()))
                except Exception:
                    pass
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
                if young and key not in tone_keys and METAL_RX.search(it["title"]) and not NOISE_RX.search(it["title"]):
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
                kind = "OFFICIAL" if always else ("DRIVER / WORLD" if DRIVER_RX.search(it["title"]) else "METALS NEWS")
                self.emit(kind, it["title"], [self.metal_line(qs), f"{it['source']}{age}"], it["link"], imp)
        if not self.caught_up:                      # show the last 2 hours of metals news once, without pop-ups
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
        for group, levels, label, big in ((METAL_MAIN, MAIN_LEVELS, "METAL MOVE", 2.0),
                                          (METAL_STOCKS, STOCK_LEVELS, "METAL STOCKS", 5.0)):
            for name, sym in group.items():
                if sym not in qs:
                    continue
                p = self.c.pct(qs[sym])
                hit = [lv for lv in levels if abs(p) >= lv]
                if not hit:
                    continue
                key = f"{day}:{sym}:{'up' if p > 0 else 'down'}:{max(hit)}"
                if key in self.st["alarms"]:
                    continue
                self.st["alarms"].append(key)
                self.emit(label, f"{name} {'UP' if p > 0 else 'DOWN'} {p:+.1f}% today", [self.metal_line(qs)],
                          imp=8 if max(hit) >= big else 0)

    def check_events(self):
        now = self.c.ny_now()
        if now.weekday() >= 5:
            return
        for t, name, words in self.calendar():
            mins = (t - now).total_seconds() / 60
            key = f"{t:%Y-%m-%d}:{name}"
            if 0 < mins <= 30 and "CFTC" not in name and key not in self.st["events"]:
                self.st["events"].append(key)
                self.emit("COMING UP", f"{name.split('  ')[-1]} at {t:%H:%M} NY (in {int(mins)} min)",
                          [words, "Gold often jumps right after this number"])

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
                    self.save()
                    self.status = f"Live · last check {self.c.ny_now():%H:%M:%S} NY"
                else:
                    self.status = f"Waiting · alerts start {self.cfg['settings']['start_ny']} NY (Mon-Fri)"
                time.sleep(max(secs, 20))
            except Exception as e:
                self.status = f"Error, retrying: {e.__class__.__name__}"
                time.sleep(30)

# ================= window =================

def _econ_box(tk, parent, F, GOLD):
    box = tk.Frame(parent, bg="#221D10", highlightbackground=GOLD, highlightthickness=1)
    box.pack(fill="x", padx=14, pady=(6, 0))
    head = tk.Label(box, text="TODAY  loading news calendar...", bg="#221D10", fg=GOLD, font=(F, 9, "bold"),
                    anchor="w", justify="left", wraplength=430)
    head.pack(fill="x", padx=8, pady=(3, 0))
    body = tk.Label(box, text="", bg="#221D10", fg="#AAAAAA", font=(F, 8), anchor="w", justify="left", wraplength=430)
    body.pack(fill="x", padx=8, pady=(0, 4))
    return head, body

def open_metals_window(tk, root, eng, style, dark_titlebar, webbrowser, others=(), place=None):
    """Compact metals panel, same size and place as the US Market Intel window."""
    GOLD, TEXT, GREY, LINE, BG, DOWN = style
    F, SOFT = "Segoe UI", "#AAAAAA"
    color = lambda p: GOLD if p > 0 else DOWN
    win = tk.Toplevel(root)
    win.title("Metals Market Intel")
    win.configure(bg=BG)
    win.attributes("-topmost", bool(root.attributes("-topmost")))
    dark_titlebar(win)

    def dock():
        root.update_idletasks()
        win.geometry(place() if place else root.geometry())
    win.after(50, dock)
    win.after(400, dock)

    def label(text, pady=(8, 1)):
        tk.Label(win, text=text, bg=BG, fg=GREY, font=(F, 9), anchor="w").pack(fill="x", padx=17, pady=pady)

    status = tk.Label(win, text="", bg=BG, fg=GREY, font=(F, 8), anchor="w")
    status.pack(side="bottom", fill="x", padx=17, pady=(2, 6))

    top = tk.Frame(win, bg=BG)
    top.pack(fill="x", padx=14, pady=(10, 2))
    tiles = {}
    for name in METAL_MAIN:
        f = tk.Frame(top, bg=BG, highlightbackground=LINE, highlightthickness=1)
        f.pack(side="left", expand=True, fill="x", padx=2)
        tk.Label(f, text=name, bg=BG, fg=GREY, font=(F, 8)).pack(pady=(3, 0))
        tiles[name] = tk.Label(f, text="--", bg=BG, fg=TEXT, font=(F, 11, "bold"))
        tiles[name].pack()
        tiles[name + "$"] = tk.Label(f, text="", bg=BG, fg=GREY, font=(F, 7))
        tiles[name + "$"].pack(pady=(0, 3))
    other_lbl = tk.Label(win, text="", bg=BG, fg=GREY, font=(F, 7), anchor="w", justify="left", wraplength=430)
    other_lbl.pack(fill="x", padx=17)
    import econ_calendar
    econ_head, econ_body = _econ_box(tk, win, F, GOLD)

    drivers_row = tk.Label(win, text="Top metals drivers today", bg=BG, fg=GOLD, font=(F, 10, "bold"), anchor="w")
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
    _, ment_lbl = box("Mentor's desk note (if this, then that)")

    label("Metals plumbing (how the market is wired today)")
    plumb = tk.Frame(win, bg=BG)
    plumb.pack(fill="x", padx=17)
    plumb_rows = []
    for _ in range(5):
        r = tk.Frame(plumb, bg=BG)
        r.pack(fill="x")
        a = tk.Label(r, text="", bg=BG, fg=TEXT, font=(F, 8, "bold"), anchor="nw", width=17)
        a.pack(side="left", anchor="n")
        b = tk.Label(r, text="", bg=BG, fg=SOFT, font=(F, 8), anchor="w", justify="left", wraplength=310)
        b.pack(side="left", fill="x")
        plumb_rows.append((a, b))

    label("Metal stocks and funds vs yesterday's close")
    stk = tk.Frame(win, bg=BG)
    stk.pack(fill="x", padx=17)
    stk_rows = {}
    for name in METAL_STOCKS:
        r = tk.Frame(stk, bg=BG)
        r.pack(fill="x")
        tk.Label(r, text=name, bg=BG, fg=TEXT, font=(F, 8), anchor="w", width=24).pack(side="left")
        c = tk.Canvas(r, width=130, height=9, bg=BG, highlightthickness=0)
        c.pack(side="left", padx=4)
        b = tk.Label(r, text="", bg=BG, fg=TEXT, font=(F, 8, "bold"), anchor="e", width=7)
        b.pack(side="right")
        stk_rows[name] = (c, b)

    label("Metals calendar (New York time)")
    cal_lbl = tk.Label(win, text="", bg=BG, fg=TEXT, font=(F, 8), anchor="w", justify="left", wraplength=430)
    cal_lbl.pack(fill="x", padx=17)

    label("Most relevant metals news (top 3)")
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
                pin.insert("end", "Nothing big yet. Fed, dollar, war headlines and big gold or silver moves appear here.", "s")
            for t in items:
                pin.insert("end", f"{t['time']}  {t['title']}\n", "t")
            pin.configure(state="disabled")
        qs = eng.prices()
        pct = eng.c.pct
        for name, sym in METAL_MAIN.items():
            if sym in qs:
                p = pct(qs[sym])
                tiles[name].configure(text=f"{'▲' if p > 0 else '▼'} {p:+.2f}%", fg=color(p))
                tiles[name + "$"].configure(text=f"{qs[sym][0]:,.2f}$")
        other_lbl.configure(text="   ".join(f"{n} {pct(qs[s]):+.1f}%" for n, s in METAL_OTHER.items() if s in qs))
        try:
            h, b = econ_calendar.text(econ_calendar.get(eng.c).banner("metals"))
            econ_head.configure(text=h)
            econ_body.configure(text=b)
        except Exception:
            pass
        r = eng.my_read()
        arrow = {"UP": "▲", "DOWN": "▼"}.get(r["bias"], "■")
        read_head.configure(text=f"My read:  {arrow} {r['bias']}  ({r['conf']} confidence)",
                            fg=GOLD if r["bias"] == "UP" else DOWN if r["bias"] == "DOWN" else TEXT)
        read_why.configure(text=(r["lead"] + "\n" if r.get("lead") else "") + "\n".join("• " + w for w in r["why"]))
        ment_lbl.configure(text="\n".join("• " + m for m in eng.mentor()))
        rows = eng.plumbing(qs)
        for i, (a, b) in enumerate(plumb_rows):
            a.configure(text=rows[i][0] if i < len(rows) else "")
            b.configure(text=rows[i][1] if i < len(rows) else "")
        sp = {n: pct(qs[s]) for n, s in METAL_STOCKS.items() if s in qs}
        biggest = max([abs(v) for v in sp.values()] + [2.0])
        for name, (c, b) in stk_rows.items():
            c.delete("all")
            c.create_line(65, 0, 65, 9, fill=LINE)
            if name in sp:
                p = sp[name]
                b.configure(text=f"{p:+.1f}%", fg=color(p))
                w = int(abs(p) / biggest * 64)
                c.create_rectangle(65, 2, 65 + w, 7, fill=GOLD, width=0) if p > 0 else c.create_rectangle(65 - w, 2, 65, 7, fill=DOWN, width=0)
            else:
                b.configure(text="--", fg=GREY)
        cal_lbl.configure(text="\n".join(f"{t}  ·  {w}" for _, t, w in eng.calendar()[:4]))
        best = eng.best_news(3)
        nsig = json.dumps([a["id"] for a in best])
        if nsig != state["news"]:
            state["news"] = nsig
            news.configure(state="normal")
            news.delete("1.0", "end")
            if not best:
                news.insert("end", "No important metals news yet today.", "meta")
            for i, a in enumerate(best):
                src = a["lines"][-1] if a.get("lines") else ""
                news.insert("end", f"{a['time']} NY · {a['kind']} · {src}\n", "meta")
                tag = f"n{a['id']}"
                news.insert("end", a["title"] + ("\n\n" if i < len(best) - 1 else ""), ("title", tag))
                if a.get("link"):
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
