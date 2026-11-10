"""
Economic calendar for all three screens (US, Oil, Metals).
Source: Forex Factory weekly feed (free) - this week's events with forecast and previous.
Shows today's and this week's big US news, and lets 'My read', the mentor note and the news ranking react to them.
"""
import json, re, threading, time
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
    r["why"] = why[:3]
    return r


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
