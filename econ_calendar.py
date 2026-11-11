"""
Economic calendar for all three screens (US, Oil, Metals).
Source: Forex Factory weekly feed (free) - this week's events with forecast and previous.
Shows today's and this week's big US news, and lets 'My read', the mentor note and the news ranking react to them.
"""
import json, os, re, threading, time
from datetime import datetime, timedelta
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
    """PC only: 2 rising beeps = HIGH confidence, 1 low beep = LOW confidence."""
    try:
        import winsound
    except ImportError:
        return                      # cloud / iPhone: no sound
    def run():
        try:
            for f, d in ([(880, 120), (1320, 180)] if kind == "high" else [(440, 400)]):
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
    key = f"{r.get('bias')}|{c}"
    prev = st.get("last_conf")
    st["last_conf"] = c
    if prev and prev != c and c in ("high", "low") and now - st.get("beep_t", 0) > 120:
        st["beep_t"] = now
        _beep(c)
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


def short_mentor(r, market, notes, core=None, f=None):
    """Mentor's desk note, short: why this confidence + max 2 'if this, then that' lines
    (today's big news first, then today's big movers like VIX, yields or the dollar)."""
    lines = []
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
    lines = b["today"] or [{"text": "no big US news", "cd": "", "done": False}]
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
