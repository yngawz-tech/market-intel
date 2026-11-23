"""
Economic calendar for all three screens (US, Oil, Metals).
Source: Forex Factory weekly feed (free) - this week's events with forecast and previous.
Shows today's and this week's big US news, and lets 'My read', the mentor note and the news ranking react to them.
"""
import json, os, re, threading, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
CACHE_FILE = HERE / "state_calendar.json"
URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36", "Accept": "application/json"}
REFRESH, RETRY = 3600, 600          # the feed asks not to be checked often: once an hour is plenty

# title pattern, short name, simple meaning, always big?, oil only?
_KEYS = [
    (r"non-farm|nonfarm", "NFP", "jobs report - strong number: dollar and yields up, NQ and gold down; weak: the opposite", True, False),
    (r"\bcpi\b", "CPI", "inflation - hot number: yields and dollar up, NQ and gold down; cool number: the opposite", True, False),
    (r"federal funds rate|fomc statement|fomc press conference", "FOMC", "Fed rate decision - biggest mover of the month; the first spike often reverses", True, False),
    (r"fomc meeting minutes", "FOMC minutes", "notes from the last Fed meeting - can move yields, NQ and gold", True, False),
    (r"fed chair|powell", "Fed Chair", "Fed Chair speaks - one sentence can move rates, NQ and gold", True, False),
    (r"\bpce\b", "PCE", "the Fed's favourite inflation number - hot: NQ and gold down", True, False),
    (r"\bppi\b", "PPI", "factory prices - early inflation signal; hot number: yields up, NQ down", True, False),
    (r"\bgdp\b", "GDP", "economy growth - weak number: recession fears, yields down", True, False),
    (r"retail sales", "Retail sales", "consumer spending - strong number: dollar and yields up", True, False),
    (r"\bism\b", "ISM", "business survey - moves yields and the dollar", True, False),
    (r"jolts", "JOLTS", "job openings - how strong the jobs market is", True, False),
    (r"unemployment claims", "Jobless claims", "weekly jobs number - a big surprise moves the dollar and gold", False, False),
    (r"uom consumer sentiment|uom inflation", "Consumer mood", "how people feel - inflation expectations matter most", False, False),
    (r"fomc member|fed governor|fed vice chair", "Fed speaker", "a Fed official talks - can move rates", False, False),
    (r"bank holiday", "US holiday", "thin trading, bond market closed", True, False),
    (r"crude oil inventories", "EIA oil stocks", "official US oil storage - big oil mover", True, True),
    (r"api weekly", "API oil stocks", "early estimate of US oil storage", True, True),
    (r"natural gas storage", "Gas storage", "US natural gas in storage", False, True),
]
KEYS = [(re.compile(p, re.I), s, w, big, oil) for p, s, w, big, oil in _KEYS]

# words in headlines that belong to each event (used to push those headlines up)
NEWS_WORDS = {
    "NFP": ["payrolls", "nonfarm", "non-farm", "jobs report", "unemployment rate"],
    "CPI": ["cpi", "inflation", "consumer prices", "consumer price"],
    "FOMC": ["fed", "fomc", "rate decision", "powell", "rate cut", "rate hike", "federal reserve"],
    "FOMC minutes": ["fed minutes", "fomc minutes", "minutes"],
    "Fed Chair": ["powell", "fed chair"],
    "PCE": ["pce", "inflation"],
    "PPI": ["ppi", "producer prices", "wholesale prices"],
    "GDP": ["gdp", "economic growth"],
    "Retail sales": ["retail sales"],
    "ISM": ["ism", "manufacturing", "services pmi"],
    "JOLTS": ["jolts", "job openings"],
    "Jobless claims": ["jobless claims", "unemployment claims"],
    "EIA oil stocks": ["eia", "crude inventories", "crude stocks", "stockpiles", "inventories"],
    "API oil stocks": ["api", "crude inventories", "stockpiles"],
}


def _pick(e, market):
    if e.get("country") != "USD":
        return None
    for rx, short, why, big, oil in KEYS:
        if rx.search(e.get("title", "")):
            if oil and market != "oil":
                return None
            if big or e.get("impact") in ("High", "Medium"):
                return short, why, big
            return None
    if e.get("impact") == "High":
        return e["title"], "high-impact US news", True
    return None


def countdown(t, now):
    m = int((t - now).total_seconds() // 60)
    if m > 0:
        return f"in {m // 60}h {m % 60:02d}m" if m >= 60 else f"in {m} min"
    return "just out" if m > -60 else "done"


class Calendar:
    def __init__(self, core):
        self.c, self.lock, self.raw, self.t, self.status = core, threading.Lock(), [], 0, "loading..."
        self._rx = {}
        try:
            d = json.loads(CACHE_FILE.read_text())
            self.raw, self.t, self.status = d.get("events", []), d.get("t", 0), "ok"
        except Exception:
            pass

    def refresh(self, force=False):
        if not force and time.time() - self.t < REFRESH:
            return
        try:
            raw = json.loads(self.c.http_get(URL, UA, timeout=15))
            if not isinstance(raw, list):
                raise ValueError("unexpected feed")
            with self.lock:
                self.raw, self.t, self.status = raw, time.time(), "ok"
            CACHE_FILE.write_text(json.dumps({"t": self.t, "events": raw}))
        except Exception as e:
            self.t = time.time() - REFRESH + RETRY                  # try again in 10 minutes
            if not self.raw:
                self.status = f"calendar unavailable ({e.__class__.__name__})"

    def groups(self, market):
        tz = self.c.ny_now().tzinfo
        with self.lock:
            raw = list(self.raw)
        g = {}
        for e in raw:
            p = _pick(e, market)
            if not p:
                continue
            try:
                t = datetime.fromisoformat(e["date"]).astimezone(tz)
            except Exception:
                continue
            k = (t, p[0])
            if k not in g:
                g[k] = {"t": t, "short": p[0], "why": p[1], "big": p[2], "impact": e.get("impact", ""), "items": []}
            g[k]["items"].append(e)
            if e.get("impact") == "High":
                g[k]["impact"], g[k]["big"] = "High", True
        return sorted(g.values(), key=lambda x: x["t"])

    @staticmethod
    def detail(g):
        parts = []
        for e in g["items"]:
            if e.get("forecast") or e.get("previous"):
                parts.append(f"{e['title']}: forecast {e.get('forecast') or '-'}, before {e.get('previous') or '-'}")
        return " · ".join(parts[:2])

    def banner(self, market):
        """Small 'today's news' box: today's events with countdown, what they mean, and the rest of the week."""
        now = self.c.ny_now()
        today = now.date()
        gs = self.groups(market)
        lines = []
        for g in [g for g in gs if g["t"].date() == today][:4]:
            holiday = g["short"] == "US holiday"
            cd = "" if holiday else countdown(g["t"], now)
            lines.append({"text": ("all day" if holiday else f"{g['t']:%H:%M}") + f"  {g['short']}"
                                  + ("  · HIGH" if g["impact"] == "High" and not holiday else ""),
                          "cd": cd, "detail": self.detail(g), "done": cd == "done"})
        nxt = next((g for g in gs if g["t"].date() == today and g["t"] > now - timedelta(minutes=60)), None)
        week = [g for g in gs if g["t"].date() > today and g["short"] != "Fed speaker"
                and (g["big"] or g["impact"] in ("High", "Medium"))][:5]
        return {"today": lines,
                "why": f"{nxt['short']}: {nxt['why']}" if nxt else "",
                "week": " · ".join(f"{g['t']:%a} " + ("" if g["short"] == "US holiday" else f"{g['t']:%H:%M} ")
                                   + g["short"] for g in week) or "nothing big later this week",
                "breaking": today_break(market),
                "status": self.status}

    def near(self, market, before=120, after=60):
        """The big event right before or right after now: (group, 'before'|'after') or None."""
        now = self.c.ny_now()
        for g in self.groups(market):
            if not g["big"] or g["short"] == "US holiday":
                continue
            m = (g["t"] - now).total_seconds() / 60
            if 0 < m <= before:
                return g, "before"
            if -after <= m <= 0:
                return g, "after"
        return None

    def today_rx(self, market):
        now = self.c.ny_now()
        key = (market, now.strftime("%Y-%m-%d %H"))
        if key not in self._rx:
            words = set()
            for g in self.groups(market):
                if g["t"].date() == now.date() and g["big"]:
                    words.update(NEWS_WORDS.get(g["short"], [g["short"].lower()]))
            self._rx = {key: re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(words)) + r")\b", re.I)
                        if words else None}
        return self._rx[key]


_CAL = {}
_LOCK = threading.Lock()


def get(core):
    with _LOCK:
        if "cal" not in _CAL:
            _CAL["cal"] = Calendar(core)
        return _CAL["cal"]


# ---------- helpers used by the screens ----------
def text(b):
    """(headline, small lines) for the PC windows."""
    if b["today"]:
        head = "\n".join(f"TODAY  {l['text']}" + (f"  ·  {l['cd']}" if l["cd"] else "") for l in b["today"])
    elif b.get("breaking"):
        head = "TODAY  no scheduled US news"
    else:
        head = "TODAY  no big US news"
    body = [l["detail"] for l in b["today"] if l["detail"] and not l["done"]][:2]
    if b["why"]:
        body.append(b["why"])
    body.append("This week: " + b["week"])
    if b["status"] != "ok":
        body.append(b["status"])
    return head, "\n".join(body)


def boost(core, title, market):
    """Extra importance for headlines about today's big events."""
    try:
        rx = get(core).today_rx(market)
        return 3 if rx and rx.search(title) else 0
    except Exception:
        return 0


def adjust_read(core, r, market):
    """Big news close by: say so in 'My read' (and lower confidence before the number)."""
    try:
        n = get(core).near(market)
    except Exception:
        n = None
    if not n:
        return r
    g, phase = n
    r = dict(r)
    why = list(r.get("why") or [])
    if phase == "before":
        why.insert(0, f"{g['short']} at {g['t']:%H:%M} NY ({countdown(g['t'], core.ny_now())}) - this read can flip on the number")
        r["conf"] = "low"
    else:
        why.insert(0, f"{g['short']} just came out - the first move after a big number often fakes out")
    r["why"] = why[:5 if r.get("conf") == "high" else 3]
    return r


HI_WATCH = {"us": "the 10-year yield or VIX", "oil": "the next-month gap", "metals": "the dollar or yields"}
_NOT_SIGNAL = ("came out", "can flip", "fighting", "mixed or quiet", "not loaded")


def _beep(kind):
    """PC only: 2 rising beeps = HIGH confidence, 1 low beep = LOW confidence, 3 quick high beeps = BREAKING."""
    try:
        import winsound
    except ImportError:
        return                      # cloud / iPhone: no sound
    def run():
        try:
            for f, d in {"high": [(880, 120), (1320, 180)], "breaking": [(1500, 90), (1500, 90), (1500, 90)]}.get(kind, [(440, 400)]):
                winsound.Beep(f, d)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


# ---- read timer: one start time for PC and iPhone ----
# The cloud runs 24/5 (PC on or off), so its start time is the master copy.
# The PC reads it every 2 minutes from the iPhone data file and uses it.
CLOUD_DATA = "https://yngawz-tech.github.io/market-intel/data.json"
_CLOUD, _CLOUD_ON = {}, []


def _cloud_loop(core):
    while True:
        try:
            d = json.loads(core.http_get(f"{CLOUD_DATA}?t={int(time.time())}", None, 15))
            if time.time() - d.get("updated_ts", 0) < 1200:
                for m in ("us", "oil", "metals"):
                    rr = (d.get(m) or {}).get("read") or {}
                    s = rr.get("since")
                    _CLOUD[m] = {"key": f"{rr.get('bias')}|{rr.get('conf')}", "t": s, "at": rr.get("since_at")} if s else None
        except Exception:
            pass
        time.sleep(120)


def _cloud_start(core):
    if _CLOUD_ON or os.environ.get("GITHUB_ACTIONS"):
        return                      # the cloud itself is the master copy
    _CLOUD_ON.append(1)
    threading.Thread(target=_cloud_loop, args=(core,), daemon=True).start()


def hi_conf(core, st, r, market=None):
    """Timer for HIGH and LOW reads (same direction + same confidence), and a beep when one starts.
    A short dip (under 10 min) keeps the same timer. PC and iPhone share the cloud's start time."""
    now, c = time.time(), r.get("conf")
    if r.get("bias") not in ("UP", "DOWN"):
        c = None                                        # no bias = no timer, no beep
    key = f"{r.get('bias')}|{c}"
    prev = st.get("last_conf")
    st["last_conf"] = c
    if prev and prev != c and c in ("high", "low") and now - st.get("beep_t", 0) > 120:
        st["beep_t"] = now
        _beep(c)
        if market and hasattr(core, "tg_notify"):      # same moment as the PC beep -> phone (PC only, see config.ini)
            name = {"us": "NQ / ES / YM", "oil": "OIL (CL)", "metals": "GOLD / SILVER"}.get(market, market.upper())
            why = (r.get("why") or [""])[0]
            core.tg_notify(f"MY READ · {name}\n{r.get('bias')} - {c.upper()} confidence · {core.ny_now():%H:%M} NY"
                           + (f"\n{why}" if why else ""))
    old = st.pop("hi_conf", None)                       # older version of this timer
    if old and "read_since" not in st:
        st["read_since"] = {"key": f"{old['bias']}|high", "t": old["t"], "at": old["at"], "seen": now}
    h = st.get("read_since")
    if c in ("high", "low"):
        if (not h or h["key"] != key or now - h.get("seen", now) > 1200
                or (h.get("off") and now - h["off"] > 600)):
            h = {"key": key, "t": now, "at": core.ny_now().strftime("%H:%M")}
        h.pop("off", None)
        h["seen"] = now
        if market:
            _cloud_start(core)
            cl = _CLOUD.get(market)
            # cloud saw it first, or this PC copy is old (PC was off): take the cloud's start time
            if cl and cl["key"] == key and (cl["t"] < h["t"] or h["t"] < cl["t"] - 900):
                h["t"], h["at"] = cl["t"], cl["at"]
        st["read_since"] = h
        r = dict(r, since=h["t"], since_at=h["at"])
        if c == "high":
            r.update(hi_since=h["t"], hi_at=h["at"])
    elif h and not h.get("off"):
        h["off"] = now
    return r


def hi_age(r):
    """Minutes since the read started."""
    return f"{max(1, int((time.time() - r['since']) // 60))} min"


def hi_title(r):
    """(main header, small timer text) for 'My read'."""
    arrow = {"UP": "\u25b2", "DOWN": "\u25bc"}.get(r["bias"], "\u25a0")
    conf = "HIGH" if r.get("conf") == "high" else r["conf"]
    head = f"My read:  {arrow} {r['bias']}  ({conf} confidence)"
    return (head, f"{hi_age(r)}  \u00b7  since {r['since_at']}") if r.get("since") else (head, "")


def read_row(tk, F, head, body):
    """Put the 'My read' header and the small timer on one line (PC windows)."""
    row = tk.Frame(head.master, bg=head["bg"])
    row.pack(fill="x", padx=8, pady=(3, 0), before=body)
    head.pack_forget()
    main = tk.Label(row, text="", bg=head["bg"], fg=head["fg"], font=(F, 9, "bold"), anchor="w")
    main.pack(side="left")
    small = tk.Label(row, text="", bg=head["bg"], fg="#9A9A9A", font=(F, 8), anchor="w")
    small.pack(side="left", padx=(8, 0))
    return main, small


def conf_note(r, market):
    """One short line: why the confidence is high, medium or low."""
    c, d = r.get("conf"), {"UP": "up", "DOWN": "down"}.get(r.get("bias"), "")
    why = r.get("why") or []
    n = len([w for w in why if not any(x in w for x in _NOT_SIGNAL)])
    if c == "high":
        return f"HIGH: {n} signals agree {d}. Watch {HI_WATCH.get(market, 'the main driver')}: if it turns, the edge fades."
    if c == "medium":
        return f"MEDIUM: {n} signals agree {d} - one more needed for high."
    if any("can flip" in w for w in why):
        return "LOW: big news soon - the read can flip on the number."
    return "LOW: signals mixed - no clear edge."


# "if this, then that" for each big event, per market (one short line each)
IF_EVENT = {
    "CPI": {"us": "If hot: yields up, NQ down. If cool: NQ up.",
            "metals": "If hot: dollar up, gold down. If cool: gold up.",
            "oil": "If hot: dollar up, oil under pressure."},
    "PCE": {"us": "If hot: yields up, NQ down. If cool: NQ up.",
            "metals": "If hot: dollar up, gold down. If cool: gold up."},
    "PPI": {"us": "If hot: yields up, NQ down.", "metals": "If hot: dollar up, gold down."},
    "NFP": {"us": "If jobs strong: yields up, NQ down. If weak: NQ up.",
            "metals": "If jobs strong: dollar up, gold down. If weak: gold up.",
            "oil": "If jobs weak: growth fears, oil down."},
    "FOMC": {"us": "If the Fed sounds tough: NQ down. If soft: NQ up.",
             "metals": "If the Fed sounds tough: dollar up, gold down. If soft: gold up.",
             "oil": "If the Fed sounds tough: dollar up, oil down."},
    "Jobless claims": {"us": "If claims jump: growth fears, yields down.",
                       "metals": "If claims jump: dollar down, gold up."},
    "GDP": {"us": "If weak: recession fears, yields down.", "metals": "If weak: gold up.",
            "oil": "If weak: demand fears, oil down."},
    "Retail sales": {"us": "If strong: yields up, NQ wobbles.", "metals": "If strong: dollar up, gold down.",
                     "oil": "If strong: demand hopes, oil up."},
    "ISM": {"us": "If strong: yields up.", "metals": "If strong: dollar up, gold down.",
            "oil": "If strong: demand hopes, oil up."},
    "EIA oil stocks": {"oil": "If a big draw: oil up. If a big build: oil down."},
    "API oil stocks": {"oil": "If a big draw: oil up tonight; EIA confirms on Wednesday."},
}
IF_EVENT["FOMC minutes"] = IF_EVENT["Fed Chair"] = IF_EVENT["FOMC"]


def _event_line(core, market):
    """The next big event today (or tomorrow) as one 'if this, then that' line."""
    cal = get(core)
    n = cal.near(market, before=0, after=60)
    if n:
        return f"{n[0]['short']} is out: let the first 5-min candle close - the first spike often reverses."
    now = core.ny_now()
    for g in cal.groups(market):
        days = (g["t"].date() - now.date()).days
        txt = IF_EVENT.get(g["short"], {}).get(market)
        if txt and g["t"] > now and days <= 1:
            when = f"{g['t']:%H:%M}" if days == 0 else "tomorrow"
            return f"{g['short']} {when} - {txt[0].lower() + txt[1:]}"
    return None


def _driver_lines(market, f):
    """Today's big movers as 'if this, then that' lines."""
    f, out = f or {}, []
    v, y, d = f.get("vix"), f.get("yield"), f.get("dollar")
    if market == "us":
        if v is not None and abs(v) >= 5:
            out.append("VIX rising: if it keeps climbing, bounces get sold, NQ hit hardest." if v > 0 else
                       "VIX falling: if it keeps falling, dips usually get bought.")
        if y is not None and abs(y) >= 1.5:
            out.append("Yields up: if they keep rising, NQ feels it most." if y > 0 else
                       "Yields down: if they keep falling, NQ gets help.")
        if d is not None and abs(d) >= 0.5:
            out.append("Dollar up: if it keeps rising, stocks feel the pressure." if d > 0 else
                       "Dollar down: if it keeps falling, stocks get help.")
    elif market == "metals":
        if d is not None and abs(d) >= 0.3:
            out.append("Dollar up: if it keeps rising, gold stays under pressure." if d > 0 else
                       "Dollar down: if it keeps falling, gold gets help.")
        if y is not None and abs(y) >= 1:
            out.append("Yields up: if they keep rising, gold struggles." if y > 0 else
                       "Yields down: if they keep falling, gold gets help.")
        if v is not None and v >= 5:
            out.append("VIX rising: if fear keeps growing, gold can catch safe-haven buying.")
    elif market == "oil":
        if f.get("war"):
            out.append("Attack headlines: oil spikes - if no oil is actually lost, the spike fades.")
        if f.get("peace"):
            out.append("Peace talks: oil drops fast - if the talks are denied, it snaps back.")
        if f.get("opec"):
            out.append("OPEC in the news: if they surprise on output, the move can last days.")
        w = f.get("wti")
        if w is not None and abs(w) >= 2:
            out.append(f"Oil {'up' if w > 0 else 'down'} {abs(w):.1f}%: if the headline behind it fades, price can give some back.")
    return out


# ---- one story: top driver -> My read -> mentor note (19 Nov 2026) ----
_DRV = [  # (headline words, {market: if-then}) - first match wins
    (re.compile(r"\b(refiners?|refining|refinery margins?|crack spreads?)\b", re.I),   # not "Nvidia margins"
     {"us": "Energy / refiners in focus ({d}): weak energy names weigh on YM and ES more than on NQ.",
      "metals": "Energy weakness in focus ({d}): falling oil cools inflation fears - less support for gold.",
      "oil": "Refiners lead today ({d}): weak refining margins mean refiners buy less crude - a drag on CL; "
             "if margins recover, crude gets support."}),
    (re.compile(r"\b(nvidia|chip|chips|chipmakers?|semiconductors?|ai stocks|broadcom|data centers?|h200|tsmc)\b", re.I),
     {"us": "Chips lead today ({d}): if chips keep moving the same way, NQ leads ES and YM; if chips turn, NQ turns first.",
      "metals": "Risk mood comes from chips today ({d}): a tech selloff can send money to gold; a tech bounce takes it away.",
      "oil": "Tech leads the risk mood ({d}): big index moves can drag oil with them for a while."}),
    (re.compile(r"\b(fed|fomc|powell|warsh|rate hikes?|rate cuts?|yields?|treasur(y|ies)|inflation|cpi|ppi|pce|payrolls|jobs|jobless)\b", re.I),
     {"us": "Rates lead today ({d}): if yields keep rising, NQ feels it most; if yields turn down, tech usually leads back.",
      "metals": "The Fed / dollar lead today ({d}): if the dollar and yields keep rising, gold stays heavy; if they turn, gold bounces.",
      "oil": "The Fed leads today ({d}): a stronger dollar weighs on oil; if the dollar turns, oil gets relief."}),
    (re.compile(r"\b(oil|crude|opec|hormuz|tankers?|iran|houthis?|red sea|refiner(y|ies)|brent|wti)\b", re.I),
     {"us": "Oil / war lead today ({d}): if oil spikes, risk-off hits NQ hardest; if oil drops, stocks usually get relief.",
      "metals": "War / oil headlines lead ({d}): escalation usually helps gold as a safe haven; calm takes that support away.",
      "oil": "Supply risk leads today ({d}): if it escalates, CL usually spikes first; if it calms, the war premium fades."}),
    (re.compile(r"\b(tariffs?|trade|china|export controls|sanctions|xi)\b", re.I),
     {"us": "Trade / China lead today ({d}): more tariff or export news hits chips (NQ) and industrials (YM) first.",
      "metals": "Trade / China lead today ({d}): rising tension usually supports gold; a deal takes safe-haven demand away.",
      "oil": "Trade / China lead today ({d}): China demand worries weigh on oil; a deal lifts demand hopes."}),
    (re.compile(r"\b(dollar|gold|silver|bullion|miners?)\b", re.I),
     {"us": "Dollar / metals in focus ({d}): a strong dollar is a headwind for big US tech.",
      "metals": "Gold leads its own story ({d}): if the move holds into the US open, it often runs; if it fades by 10:00, it reverses.",
      "oil": "The dollar is in focus ({d}): a strong dollar usually weighs on oil."}),
]

def _short(t, n=60):
    t = (t or "").strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "..."

def top_driver(top):
    """The #1 driver = the most important story in the drivers box (the box itself keeps its own order)."""
    return max(top, key=lambda t: t.get("impact", 0)) if top else None

def driver_if_then(top, market):
    """If-then line built from the #1 top driver (same story as the drivers box)."""
    t = top_driver(top)
    if not t:
        return ""
    d = t.get("title", "")
    for rx, txt in _DRV:
        if rx.search(d):
            return txt[market].format(d=f'"{_short(d)}"')
    return f'Top driver "{_short(d)}": if follow-up news confirms it, the move usually extends; if not, it fades.'

def with_driver(r, top):
    """My read shows the #1 top driver as its first reason, so read, drivers box and mentor tell one story."""
    t = top_driver(top)
    if not t:
        return r
    d = f"driver: {_short(t.get('title', ''), 70)} ({t.get('time', '')} NY)"
    why = [w for w in (r.get("why") or []) if not w.startswith("driver:") and w != "signals are mixed or quiet"]
    return dict(r, why=[d] + why, driver=t.get("title", ""))


def short_mentor(r, market, notes, core=None, f=None, top=None):
    """Mentor's desk note, short: why this confidence + max 2 'if this, then that' lines
    (the #1 top driver first, then today's big news, then today's big movers like VIX, yields or the dollar)."""
    lines = []
    dl = driver_if_then(top, market)
    if dl:
        lines.append(dl)
    try:
        e = _event_line(core, market) if core else None
        if e:
            lines.append(e)
    except Exception:
        pass
    lines += _driver_lines(market, f)
    if not lines and notes:
        lines.append(notes[0].split(" - ")[0].rstrip(".") + ".")
    return [conf_note(r, market)] + lines[:2]


def mentor_note(core, market):
    try:
        n = get(core).near(market, before=180, after=60)
    except Exception:
        n = None
    if not n:
        return None
    g, phase = n
    if phase == "before":
        return (f"{g['short']} at {g['t']:%H:%M} NY - {g['why']}. Big players often wait for it: "
                f"moves just before the number are often traps.")
    return (f"{g['short']} is out - let the first 5-minute candle close before you trust the direction; "
            f"the first spike often reverses.")


# ---------- PC window: today's lines, done ones faded (same look as the iPhone) ----------
BOX_BG, DONE_FG, CD_FG = "#221D10", "#7B621E", "#DADADA"


def make_head(tk, parent, F, gold):
    """Holder for the TODAY lines (one row per event)."""
    f = tk.Frame(parent, bg=BOX_BG)
    f._tk, f._F, f._gold, f._sig = tk, F, gold, None
    tk.Label(f, text="TODAY  loading news calendar...", bg=BOX_BG, fg=gold, font=(F, 9, "bold"),
             anchor="w").pack(fill="x")
    return f


def paint(head, body, b):
    """Draw the banner: finished events faded, countdown in plain text - like the iPhone."""
    lines = b["today"] or [{"text": "no scheduled US news" if b.get("breaking") else "no big US news",
                            "cd": "", "done": False}]
    sig = tuple((l["text"], l["cd"], l["done"]) for l in lines)
    if sig != head._sig:
        head._sig = sig
        tk, F = head._tk, head._F
        for w in head.winfo_children():
            w.destroy()
        for l in lines:
            row = tk.Frame(head, bg=BOX_BG)
            row.pack(fill="x")
            tk.Label(row, text=f"TODAY  {l['text']}", bg=BOX_BG, fg=DONE_FG if l["done"] else head._gold,
                     font=(F, 9, "bold"), anchor="w").pack(side="left")
            if l["cd"]:
                tk.Label(row, text=f" · {l['cd']}", bg=BOX_BG, fg=DONE_FG if l["done"] else CD_FG,
                         font=(F, 8), anchor="w").pack(side="left")
    body.configure(text=text(b)[1])


# ================= price in sync / out of sync (Oil + Metals, replaces "plumbing") =================
# Drivers = the panel's sector stocks vs yesterday's close (+ news mood). Price push = main future, last 30 min.
# In sync = price moves the way the drivers push. Out of sync = price moves against them (possible run / trap).
_M30 = {}

def move30(c, sym):
    """% move of sym over the last 30 min (Yahoo 5m bars), cached 60 s. None if not loaded."""
    hit = _M30.get(sym)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        import urllib.parse
        d = json.loads(c.http_get("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym)
                                  + "?interval=5m&range=1d&includePrePost=true"))["chart"]["result"][0]
        cl = [x for x in d["indicators"]["quote"][0]["close"] if x is not None]
        v = (cl[-1] / cl[-7] - 1) * 100 if len(cl) >= 7 and cl[-7] else None
    except Exception:
        v = hit[1] if hit else None
    _M30[sym] = (time.time(), v)
    return v

# ---- news push: Forex Factory released numbers (actual vs forecast) + headline mood (last 3 h) ----
_FF = {"t": 0, "ev": []}
_INFL = re.compile(r"CPI|PPI|PCE|Price|Inflation|Earnings", re.I)

def ff_released(c):
    """Today's released USD high/medium events from the Forex Factory calendar page (cached 5 min)."""
    if time.time() - _FF["t"] < 300:
        return _FF["ev"]
    _FF["t"] = time.time()
    try:
        now = c.ny_now()
        day = f"{now:%b}".lower() + f"{now.day}.{now.year}"
        html = c.http_get(f"https://www.forexfactory.com/calendar?day={day}", {"User-Agent": UA["User-Agent"]}, timeout=15)
        html = html.decode("utf-8", "replace") if isinstance(html, bytes) else html
        bs, seen, ev = chr(92), set(), []
        for m in re.finditer(r'\{"id":\d+,"ebaseId".*?"showExpanded":(?:true|false)', html):
            t = m.group(0)
            def g(k):
                x = re.search('"' + k + '":("(?:[^"' + bs * 2 + ']|' + bs * 2 + '.)*"|-?[0-9]+)', t)
                return json.loads(x.group(1)) if x else None
            key = (g("name"), g("dateline"))
            if (g("currency") != "USD" or not g("actual") or key in seen
                    or (g("impactName") not in ("high", "medium") and "Crude Oil Inventories" not in (g("name") or ""))):
                continue
            seen.add(key)
            ev.append({"name": g("name"), "t": int(g("dateline") or 0), "impact": g("impactName"),
                       "actual": g("actual"), "forecast": g("forecast") or "", "bw": int(g("actualBetterWorse") or 0)})
        _FF["ev"] = ev
    except Exception:
        pass                                            # keep the last good list; headline mood still works
    return _FF["ev"]

def _num(x):
    try:
        return float(re.sub(r"[^0-9.+-]", "", x))
    except Exception:
        return None

def news_push(c, market, tones, hours=3):
    """(-1 / 0 / +1, short text). market: us / oil / metals."""
    cut, score, txt = time.time() - hours * 3600, 0, ""
    for e in sorted(ff_released(c), key=lambda e: e["t"]):
        if e["t"] < cut or e["t"] > time.time():
            continue
        w, v = (2 if e["impact"] == "high" else 1), 0
        if market == "oil":
            if "Crude Oil Inventories" in e["name"] and _num(e["actual"]) is not None and _num(e["forecast"]) is not None:
                v = 1 if _num(e["actual"]) < _num(e["forecast"]) else -1 if _num(e["actual"]) > _num(e["forecast"]) else 0
        elif e["bw"] in (1, 2):
            better = 1 if e["bw"] == 1 else -1                  # better / worse for the US dollar
            if market == "metals":
                v = -better                                     # strong dollar data = pressure on gold
            else:
                v = -better if _INFL.search(e["name"]) else better   # hot inflation = bad for stocks, strong growth = good
        if v:
            score += v * w
            txt = f"{e['name']} {e['actual']} vs {e['forecast'] or '-'}"
    up = dn = 0
    for t in tones or []:
        ts = t.get("ts")
        if ts is None and t.get("t"):
            try:
                ts = datetime.fromisoformat(t["t"]).timestamp()
            except Exception:
                ts = None
        if ts and ts >= cut:
            up += t.get("v", 0) > 0
            dn += t.get("v", 0) < 0
    score += 1 if up - dn >= 2 else -1 if dn - up >= 2 else 0
    return (1 if score > 0 else -1 if score < 0 else 0), txt

def sync_state(c, price_sym, price_name, drivers, news=0, news_txt="", read=None):
    """One story with My read: the read's bias IS the drivers' direction (sectors + news are its evidence).
    Only when My read has no clear bias do the sectors + news decide. Sector count uses the read's rule (> 0 = up).
    drivers: {name: % vs yesterday's close} (already flipped where 'up is bad'). news: -1 / 0 / +1."""
    vals = [p for p in drivers.values() if p is not None]
    up, dn = sum(1 for p in vals if p > 0), sum(1 for p in vals if p < 0)
    bias = (read or {}).get("bias")
    if bias in ("UP", "DOWN"):
        dsign, who = (1 if bias == "UP" else -1), f"My read {bias}"
    else:
        drv = (up - dn) / max(len(vals), 1) + 0.5 * news
        dsign, who = (1 if drv >= 0.3 else -1 if drv <= -0.3 else 0), "sectors + news"
    word = lambda x: "up" if x > 0 else "down"
    m = move30(c, price_sym)
    psign = 0 if m is None or abs(m) < 0.05 else (1 if m > 0 else -1)
    if not vals or m is None:
        state, story = "LOADING", "waiting for prices"
    elif dsign == 0:
        state, story = "QUIET", "My read has no clear bias and sectors are mixed - no backing either way"
    elif bias not in ("UP", "DOWN"):                  # no read bias -> never call it backed (one story with My read)
        lean = "with" if psign == dsign else "against" if psign else "flat vs"
        state, story = "QUIET", (f"My read: no clear bias - sectors lean {word(dsign)} ({up} up, {dn} down), "
                                 f"price {lean} them - wait for the read to confirm")
    elif psign == 0:
        state, story = "QUIET", f"price flat for 30 min - {who} still {word(dsign)}, wait for the move"
    elif dsign == psign:
        state, story = "IN SYNC", f"{price_name} {word(psign)} with {who} - {up} sectors up, {dn} down - the move is backed"
    else:
        state, story = "OUT OF SYNC", (f"{price_name} {word(psign)} against {who} - {up} sectors up, {dn} down - "
                                       f"no backing, watch for a fail back {word(dsign)}")
    if news and dsign and news != dsign:
        story += f"  ·  news points {word(news)} - against the read, size down"
    if news_txt:
        story += f"  ·  data: {news_txt}"
    return {"state": state, "m30": m, "up": up, "down": dn, "news": news, "story": story, "name": price_name,
            "dir": dsign, "who": who}

def sync_mentor(s):
    """One if-then line for the Mentor note, so the note and the sync box tell the same story."""
    if s.get("state") != "OUT OF SYNC":
        return ""
    w = "up" if s["dir"] > 0 else "down"
    return (f"Price is out of sync with {s['who']} ({w}): if this move fails back {w}, the read holds - "
            f"if it holds 30 min more, the read may be turning.")

def sync_box(tk, win, F, BG, LINE):
    """Small box: state + price arrow on top, 3 mini tiles, one story line."""
    b = tk.Frame(win, bg=BG, highlightbackground=LINE, highlightthickness=1)
    b.pack(fill="x", padx=14, pady=(6, 0))
    top = tk.Frame(b, bg=BG)
    top.pack(fill="x", padx=8, pady=(3, 0))
    head = tk.Label(top, text="Price sync", bg=BG, fg="#DADADA", font=(F, 10, "bold"), anchor="w")
    head.pack(side="left")
    price = tk.Label(top, text="", bg=BG, fg="#8A8A8A", font=(F, 8), anchor="e")
    price.pack(side="right")
    row = tk.Frame(b, bg=BG)
    row.pack(fill="x", padx=8, pady=(3, 0))
    tiles = []
    for name in ("sectors up", "sectors down", "news"):
        f = tk.Frame(row, bg=BG, highlightbackground=LINE, highlightthickness=1)
        f.pack(side="left", expand=True, fill="x", padx=2)
        v = tk.Label(f, text="--", bg=BG, fg="#DADADA", font=(F, 10, "bold"))
        v.pack()
        tk.Label(f, text=name, bg=BG, fg="#8A8A8A", font=(F, 7)).pack(pady=(0, 2))
        tiles.append(v)
    story = tk.Label(b, text="", bg=BG, fg="#AAAAAA", font=(F, 8), anchor="w", justify="left", wraplength=420)
    tk.Frame(b, bg=BG, height=4).pack(fill="x")        # story line removed (Valentin 23 Nov): state + tiles say it
    return {"frame": b, "head": head, "price": price, "tiles": tiles, "story": story}

def paint_sync(w, s):
    G, R, GR, T = "#8FD19E", "#E88A8A", "#8A8A8A", "#DADADA"
    col = {"IN SYNC": G, "OUT OF SYNC": R}.get(s["state"], GR)
    w["frame"].configure(highlightbackground=col if s["state"] in ("IN SYNC", "OUT OF SYNC") else "#2E2E2E")
    w["head"].configure(text={"IN SYNC": "In sync", "OUT OF SYNC": "Out of sync", "QUIET": "Quiet",
                              "LOADING": "Price sync"}[s["state"]], fg=col if col != GR else T)
    m = s["m30"]
    w["price"].configure(text="" if m is None else f"{s['name']} {'▲' if m > 0 else '▼'} {m:+.2f}% (30 min)",
                         fg=GR if m is None or abs(m) < 0.05 else G if m > 0 else R)
    up, dn, nw = w["tiles"]
    up.configure(text=str(s["up"]), fg=G if s["up"] else T)
    dn.configure(text=str(s["down"]), fg=R if s["down"] else T)
    nw.configure(text={1: "▲ up", -1: "▼ down", 0: "mixed"}[s["news"]], fg={1: G, -1: R, 0: T}[s["news"]])
    w["story"].configure(text=s["story"])


# ================= BREAKING line + MARKET REACTED pin (Valentin, 23 Nov 2026) =================
# Breaking = market-shaking news only (top-10 company CEO change / scandal / probe, Fed or president surprise, war).
#   US + Metals panels, max 2 lines, published NY time, clears after 3 h, PC beep once per new item.
# Market reacted = a panel's OWN market made a crazy move -> the news behind it stays pinned on THAT panel
#   (US: NQ/ES/YM, Metals: gold/silver, Oil: CL). One story can sit on 1, 2 or 3 panels.
#   Crazy = 30-min move or day move >= 2.5 x its 60-day median (tested on the latest 3 years: about 1-2 a month
#   per market, stable in- and out-of-sample; fixed % levels drifted). Pin stays until the next crazy move with a
#   different story, a big new story (impact >= 10, other subject) takes #1 in that panel's box, or 18:00 NY.
BREAK_FEEDS = [("BRK FinancialJuice", "https://www.financialjuice.com/feed.ashx?xy=rss", 60, False),
               ("BRK White House", "https://www.whitehouse.gov/news/feed/", 60, False)]
REACT_SYMS = {"us": {"NQ": "NQ=F", "ES": "ES=F", "YM": "YM=F"},
              "metals": {"Gold": "GC=F", "Silver": "SI=F"},
              "oil": {"CL": "CL=F"}}
K_FAST, K_DAY, LINK_MIN, BREAK_HOURS = 2.5, 2.5, 60, 3

_TOP10 = (r"nvidia|microsoft|apple|alphabet|google|amazon|meta platforms|meta's|meta|broadcom|tesla|berkshire|jpmorgan"
          r"|jensen huang|huang|nadella|tim cook|pichai|jassy|zuckerberg|hock tan|elon musk|musk|buffett|jamie dimon|dimon")
_CEO_EVT = (r"reinstat\w*|resign\w*|steps? down|stepping down|ousted|oust\w*|fired|fires|sacked|replaced|replaces|departure"
            r"|exit|quits?|returns? as|back as|rehire\w*|restores?|probe\w*|investigat\w*|scandal|arrested|charged|indicted"
            r"|subpoena\w*|sued by the sec|affair")
_ALIAS = {"jensen huang": "nvidia", "huang": "nvidia", "nadella": "microsoft", "tim cook": "apple", "pichai": "alphabet",
          "google": "alphabet", "jassy": "amazon", "zuckerberg": "meta", "meta platforms": "meta", "meta's": "meta",
          "hock tan": "broadcom", "elon musk": "tesla", "musk": "tesla", "buffett": "berkshire", "jamie dimon": "jpmorgan",
          "dimon": "jpmorgan"}
_EVT_CLASS = [("return", r"reinstat\w*|returns? as|back as|rehire\w*|restores?"),
              ("exit", r"resign\w*|steps? down|stepping down|ousted|oust\w*|fired|fires|sacked|replaced|replaces|departure|exit|quits?"),
              ("probe", r"probe\w*|investigat\w*|arrested|charged|indicted|subpoena\w*|sued by the sec|scandal|affair")]
BRK_RX = [
    ("company", re.compile(rf"\b({_TOP10})\b.*\b(ceo|chief executive|founder|chairman)\b.*\b({_CEO_EVT})\b"
                           rf"|\b({_TOP10})\b.*\b({_CEO_EVT})\b.*\b(ceo|chief executive)\b"
                           rf"|\b(ceo|chief executive)\b.*\b({_TOP10})\b.*\b({_CEO_EVT})\b", re.I)),
    ("fed", re.compile(r"\b(fed|federal reserve|fomc|powell)\b.*\b(emergency|unscheduled|inter-?meeting)\b"
                       r"|\b(emergency|unscheduled|inter-?meeting)\b.*\b(fed|federal reserve|fomc)\b"
                       r"|\b(fires?|fired|oust\w*|remov\w*)\b.*\b(powell|fed chair\w*)\b"
                       r"|\b(powell|fed chair\w*)\b.*\b(fired|ousted|removed|resign\w*|steps? down)\b", re.I)),
    ("president", re.compile(r"^\W*(president |us president |trump|white house)\b.{0,40}\b(declares? (a )?national emergency|martial law"
                             r"|invokes|is shot|was shot|hospitali[sz]ed|resigns|fires (the )?fed|tariffs? on all|100% tariffs?)\b"
                             r"|\b(house votes to impeach|assassination attempt)\b", re.I)),
    ("war", re.compile(r"(?=.*\b(us|u\.s\.|united states|america|iran|israel|russia|china|nato|north korea|taiwan)\b)"
                       r".*\b(declares? war|invad(es|ed)|launch(es|ed) (an? )?(invasion|missile strikes?|air ?strikes?|strikes?|attack)"
                       r"|nuclear (strike|attack|test)|troops (enter|cross)\w*)\b", re.I)),
]
BRK_NOT = re.compile(r"\b(trade war|price war|cold war|culture war|war of words|chip war|bidding war|could|might|why|how|what"
                     r"|should|will|would|analyst\w*|opinion|podcast|timeline|explained|explainer|lessons|history|anniversary"
                     r"|movie|book|watch|poll|threat\w*|archives?|report says|videocast|condemn\w*|latest|possible|alleged|denies"
                     r"|warns?|if|before|ex-|former|calls? (on|for)|urges?|demands?|supports?|backs?|wants?|pushes"
                     r"|weighs?|weighing|while|calling|mulls?|considers?|surrendered|report claims|won.t|will not|not)\b"
                     r"|\?\s*$|\s\|\s", re.I)


def _entity(kind, t):
    if kind == "company":                                 # the company nearest the word CEO
        names = [(m.start(), _ALIAS.get(m.group(1).lower(), m.group(1).lower()))
                 for m in re.finditer(rf"\b({_TOP10})\b", t, re.I)]
        ceo = re.search(r"\b(ceo|chief executive|founder|chairman)\b", t, re.I)
        if not names:
            return ""
        return min(names, key=lambda x: abs(x[0] - (ceo.start() if ceo else 0)))[1]
    if kind == "war":
        m = re.search(r"\b(iran|israel|russia|ukraine|china|taiwan|north korea|us|nato|poland|india|pakistan)\b", t, re.I)
        return m.group(1).lower() if m else ""
    return kind


def _event(kind, t):
    if kind != "company":
        return kind
    for name, rx in _EVT_CLASS:
        if re.search(rf"\b({rx})\b", t, re.I):
            return name
    return "other"


def breaking_kind(title):
    """'company' / 'fed' / 'president' / 'war' for market-shaking headlines, else None. Strict on purpose."""
    t = re.sub(r"^\s*FinancialJuice:\s*", "", title or "")
    if BRK_NOT.search(t):
        return None
    for kind, rx in BRK_RX:
        if rx.search(t):
            return kind
    return None


def _ny(c, dt):
    """UTC datetime -> NY 'HH:MM' with the core's DST rule."""
    if dt is None:
        return c.ny_now().strftime("%H:%M")
    now_ny = c.ny_now()
    return dt.astimezone(now_ny.tzinfo).strftime("%H:%M")


def _sess(c):
    """Session day: a new one starts at 18:00 NY."""
    return (c.ny_now() + timedelta(hours=6)).strftime("%Y-%m-%d")


_BRK = {"st": None}


def _brk_store(st=None):
    if st is not None:
        _BRK["st"] = st
        st.setdefault("breaking", [])
    return _BRK["st"]["breaking"] if _BRK["st"] is not None else []


def breaking_seen(c, st, it):
    """Called for every fresh headline (normal feeds + BREAK_FEEDS). Adds market-shaking news, beeps once."""
    kind = breaking_kind(it.get("title"))
    if not kind or c.junk(it["title"]):
        return False
    items = _brk_store(st)
    now = time.time()
    pub = it.get("time")
    pub_ts = pub.timestamp() if pub else now
    if now - pub_ts > 3600:                               # old news is not breaking
        return False
    title = re.sub(r"^\s*FinancialJuice:\s*", "", it["title"]).strip()
    w = c.words(title)
    key = f"{kind}:{_entity(kind, title)}:{_event(kind, title)}"
    keep = 86400                                            # one line per company + event (probe / exit / return) a day
    for b in items:                                       # same story = keep the first (earliest) line
        if b.get("key") == key and now - b["ts"] < keep:
            return False
        same_evt = b.get("key", "").split(":")[::2] == key.split(":")[::2]     # probe never blocks a later return
        if same_evt and now - b["ts"] < BREAK_HOURS * 3600 and (len(w & set(b["w"])) >= 3 or c.is_duplicate(title, [b["w"]])):
            return False
    items.append({"ts": pub_ts, "time": _ny(c, pub), "title": title, "kind": kind, "w": sorted(w), "key": key})
    del items[:-40]
    _beep("breaking")
    return True


def breaking_init(st):
    """The US engine's saved state holds the Breaking list (PC + cloud); Metals reads the same list."""
    _brk_store(st)


def breaking_lines(c=None, st=None):
    """Up to 2 active Breaking items, newest first."""
    now = time.time()
    return sorted([b for b in _brk_store(st) if now - b["ts"] < BREAK_HOURS * 3600],
                  key=lambda b: -b["ts"])[:2]


# ---------- Market reacted ----------
def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def _yahoo(c, sym, interval, rng):
    import urllib.parse
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym)
           + f"?interval={interval}&range={rng}&includePrePost=true")
    d = json.loads(c.http_get(url))["chart"]["result"][0]
    q = d["indicators"]["quote"][0]
    return d["timestamp"], q


def _baseline(c, st, sym):
    """60-day medians: biggest 30-min move per session and biggest day move (vs prior close). Once a day."""
    base = st.setdefault("react_base", {})
    day = c.ny_now().strftime("%Y-%m-%d")
    b = base.get(sym)
    if b and b.get("day") == day:
        return b
    try:
        ts, q = _yahoo(c, sym, "5m", "60d")
        per = {}
        for t, x in zip(ts, q["close"]):
            if x is not None:
                per.setdefault(datetime.fromtimestamp(t + 2 * 3600, timezone.utc).date(), []).append(x)   # ~18:00 NY boundary
        fast = []
        for xs in per.values():
            if len(xs) > 30:
                fast.append(max(abs(xs[i] / xs[i - 6] - 1) for i in range(6, len(xs))) * 100)
        ts2, q2 = _yahoo(c, sym, "1d", "6mo")
        rows = [(h, l, cl) for h, l, cl in zip(q2["high"], q2["low"], q2["close"]) if None not in (h, l, cl)]
        dm = [max(abs(h / p[2] - 1), abs(l / p[2] - 1)) * 100 for p, (h, l, _) in zip(rows, rows[1:])][-60:]
        b = {"day": day, "fast": _median(fast[-60:]), "dayv": _median(dm)}
        if b["fast"] and b["dayv"]:
            base[sym] = b
    except Exception:
        pass
    return base.get(sym)


def _fast_window(c, sym):
    """Biggest 30-min move whose end is in the last 60 min: (pct, start_ts, end_ts) from today's 5-min bars."""
    ser = getattr(c, "SERIES", {}).get(sym)
    if not ser or len(ser) < 8:
        return None
    now = time.time()
    best = None
    for i in range(6, len(ser)):
        t1, x1 = ser[i]
        if now - t1 > LINK_MIN * 60:
            continue
        t0, x0 = ser[i - 6]
        m = (x1 / x0 - 1) * 100
        if best is None or abs(m) > abs(best[0]):
            best = (m, t0, t1)
    return best


def _move_at(c, sym, t0, t1):
    ser = getattr(c, "SERIES", {}).get(sym) or []
    a = next((x for t, x in ser if t >= t0), None)
    b = next((x for t, x in reversed(ser) if t <= t1), None)
    return (b / a - 1) * 100 if a and b else None


def _same_subject(c, a, b):
    wa, wb = c.words(a), c.words(b)
    sa, sb = c.subject_of(a), c.subject_of(b)
    return (sa and sa == sb) or len(wa & wb) >= 2


def check_reacted(c, st, panel, qs, top_items):
    """Run with every price check. Pins the news behind a crazy move of THIS panel's market."""
    syms = REACT_SYMS[panel]
    sess = _sess(c)
    pin = st.get("reacted")
    if st.get("reacted_done", {}).get("sess") != sess:
        st["reacted_done"] = {"sess": sess, "titles": []}  # stories already replaced this session
    done = st["reacted_done"]["titles"]
    if pin and pin.get("sess") != sess:
        pin = st["reacted"] = None                          # new session at 18:00 NY
    if pin and top_items:                                   # a big new story took #1 in this panel's box
        t0 = top_items[0]
        if (t0.get("impact", 0) >= 10 and t0.get("time", "") > pin.get("set_hm", "99")
                and not _same_subject(c, t0["title"], pin["title"])):
            done.append(pin["title"])
            pin = st["reacted"] = None
    hit = None
    for name, sym in syms.items():
        b = _baseline(c, st, sym)
        if not b:
            continue
        fw = _fast_window(c, sym)
        if fw and abs(fw[0]) >= K_FAST * b["fast"]:
            hit = ("fast", fw[1], fw[2])
            break
        if sym in qs and abs(c.pct(qs[sym])) >= K_DAY * b["dayv"]:
            hit = hit or ("day", None, None)
    if not hit:
        return st.get("reacted")
    kind, t0, t1 = hit
    if pin and (kind == "day" or (pin.get("t1") and t1 and t1 <= pin["t1"] + 1800)):
        return pin                                          # same move, keep the pin
    story = None
    now_ny = c.ny_now()
    s0 = now_ny.replace(hour=18, minute=0, second=0, microsecond=0)
    s0 = (s0 - timedelta(days=1) if now_ny < s0 else s0).timestamp()      # this session started 18:00 NY
    if kind == "fast":                                      # breaking news just before / during the move wins
        cands = [x for x in _brk_store() if t0 - LINK_MIN * 60 <= x["ts"] <= t1]
    else:                                                   # big day move: today's breaking story wins
        cands = [x for x in _brk_store() if x["ts"] >= s0]
    fresh = lambda t: not any(_same_subject(c, t, d) for d in done)
    cands = [x for x in cands if fresh(x["title"])]
    if cands:
        x = max(cands, key=lambda x: x["ts"])
        story = {"title": x["title"], "time": x["time"]}
    if story is None and top_items and fresh(top_items[0]["title"]):
        story = {"title": top_items[0]["title"], "time": top_items[0]["time"]}
    if story is None:
        return pin
    if pin and _same_subject(c, story["title"], pin["title"]):
        return pin
    if kind == "fast":
        parts = [[n, round(m, 2)] for n, m in ((n, _move_at(c, s, t0, t1)) for n, s in syms.items()) if m is not None]
        suffix = "in 30 min"
    else:
        parts = [[n, round(c.pct(qs[s]), 2)] for n, s in syms.items() if s in qs]
        suffix = "today"
    move = " · ".join(f"{n} {m:+.1f}%" for n, m in parts) + " " + suffix
    st["reacted"] = {"sess": sess, "set_hm": c.ny_now().strftime("%H:%M"), "t1": t1, "kind": kind,
                     "title": story["title"], "time": story["time"], "move": move, "parts": parts, "suffix": suffix}
    return st["reacted"]


def reacted_lines(c, st, panel, qs):
    """(title line, move line, now line) for the screens, or None."""
    pin = st.get("reacted")
    if not pin or pin.get("sess") != _sess(c):
        return None
    now = [[n, round(c.pct(qs[s]), 2)] for n, s in REACT_SYMS[panel].items() if s in qs] if pin["kind"] == "fast" else []
    return {"title": f"{pin['title']} ({pin['time']} NY)",
            "parts": pin.get("parts") or [], "suffix": pin.get("suffix", ""), "now": now}


def today_break(market):
    """Short Breaking text for the TODAY box (US + Metals only)."""
    if market not in ("us", "metals"):
        return ""
    b = breaking_lines()
    if not b:
        return ""
    t = b[0]["title"]
    return t if len(t) <= 60 else t[:57].rsplit(" ", 1)[0] + "..."


def make_top_lines(tk, parent, F, before, bg):
    """PC: red Breaking line + red-framed Market reacted box right under the price tiles. Hidden when empty.
    Returns update(c, panel, st, qs, breaking=True)."""
    RED, WHITE = "#E24B4A", "#FFFFFF"
    brk = tk.Label(parent, text="", bg="#3A1414", fg=WHITE, font=(F, 9, "bold"), anchor="w", justify="left",
                   wraplength=430, padx=8, pady=3)
    box = tk.Frame(parent, bg=bg, highlightbackground=RED, highlightthickness=1)
    head = tk.Label(box, text="MARKET REACTED", bg=bg, fg=RED, font=(F, 8, "bold"), anchor="w")
    head.pack(fill="x", padx=8, pady=(3, 0))
    body = tk.Label(box, text="", bg=bg, fg="#DADADA", font=(F, 9), anchor="w", justify="left", wraplength=430)
    body.pack(fill="x", padx=8)
    sub = tk.Text(box, bg=bg, fg="#AAAAAA", font=(F, 9), relief="flat", height=2, width=10, wrap="word",
                  highlightthickness=0, padx=0, pady=0)
    sub.tag_configure("up", foreground="#8FD19E")          # same light green / red as the price tiles
    sub.tag_configure("dn", foreground="#E88A8A")
    sub.pack(fill="x", padx=8, pady=(0, 4))
    shown = {"brk": None, "box": None}

    def update(c, panel, st, qs, breaking=True):
        lines = breaking_lines() if breaking else []
        txt = "\n".join(f"BREAKING {b['time']} NY  {b['title']}" for b in lines)
        if txt != shown["brk"]:
            shown["brk"] = txt
            if txt:
                brk.configure(text=txt)
                if shown["box"] and box.winfo_manager():
                    brk.pack(fill="x", padx=14, pady=(6, 0), before=box)    # tiles stay on top, then Breaking
                else:
                    brk.pack(fill="x", padx=14, pady=(6, 0), after=before)
            else:
                brk.pack_forget()
        r = reacted_lines(c, st, panel, qs) if st is not None else None
        key = json.dumps(r)
        if key != shown["box"]:
            shown["box"] = key
            if r:
                body.configure(text=r["title"])
                sub.configure(state="normal")
                sub.delete("1.0", "end")
                for label, parts, tail in (("", r["parts"], " " + r["suffix"]), ("now ", r["now"], " today")):
                    if not parts:
                        continue
                    if label:
                        sub.insert("end", "\n" + label)
                    for i, (n, m) in enumerate(parts):
                        sub.insert("end", (" · " if i else "") + f"{n} ")
                        sub.insert("end", f"{m:+.1f}%", "up" if m > 0 else "dn")
                    sub.insert("end", tail)
                sub.configure(state="disabled", height=2 if r["now"] else 1)
                box.pack(fill="x", padx=14, pady=(6, 0), after=brk if brk.winfo_manager() else before)
            else:
                box.pack_forget()
    return update


def drop_shown(c, items, st, panel, also=()):
    """No repeated news (Valentin 23 Nov 2026): a story shown in Breaking / Market reacted (or in `also`, e.g. the
    drivers box) is not shown again lower down. My read + mentor may still name it - they explain it."""
    above = [b["title"] for b in breaking_lines()] if panel in ("us", "metals") else []
    pin = (st or {}).get("reacted")
    if pin and pin.get("sess") == _sess(c):
        above.append(pin["title"])
    above += list(also)
    return [t for t in items if not any(_same_subject(c, t["title"], a) for a in above)]
