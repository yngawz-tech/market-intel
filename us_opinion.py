"""
'My read' and 'Mentor's desk note' for NQ, ES and YM.
Rule-based from live prices, sectors, fear gauge, yields, news mood and the calendar.
Context only - not entries, stops or advice.
"""
import re, time
from datetime import timedelta

UP_RX = re.compile(r"\b(rally|rallies|surge|surges|jump|jumps|soar|soars|rebound|rebounds|climb|climbs|gain|gains|"
                   r"record high|beats|beat|upgrade|cut rates|rate cut|easing|deal|ceasefire|peace)\b", re.I)
DOWN_RX = re.compile(r"\b(selloff|sell-off|slide|slides|plunge|plunges|tumble|tumbles|sink|sinks|drop|drops|fall|falls|"
                     r"crash|misses|miss|downgrade|warns|warning|rate hike|hike|recession|tariff|tariffs|probe|"
                     r"lawsuit|ban|war|attack|shutdown|default)\b", re.I)

def tone_of(title):
    return (1 if UP_RX.search(title) else 0) - (1 if DOWN_RX.search(title) else 0)

def record_tone(st, title, now_ts=None):
    v = tone_of(title)
    if v:
        st.setdefault("tone", []).append({"ts": now_ts or time.time(), "v": v, "title": title})
    cutoff = time.time() - 3 * 3600
    st["tone"] = [t for t in st.get("tone", []) if t["ts"] >= cutoff][-200:]

def facts(eng):
    c = eng.core
    qs = eng.prices.get()
    p = lambda s: c.pct(qs[s]) if s in qs else None
    f = {n: p(s) for n, s in c.INDEXES.items()}
    f["vix"], f["yield"], f["dollar"] = p("^VIX"), p("^TNX"), p("DX-Y.NYB")
    secs = [(n, p(s)) for n, s in c.SECTORS.items() if p(s) is not None]
    f["sectors_up"] = sum(1 for _, v in secs if v > 0)
    f["sectors_n"] = len(secs)
    f["best"] = max(secs, key=lambda x: x[1]) if secs else None
    f["worst"] = min(secs, key=lambda x: x[1]) if secs else None
    f["risk_on"] = (p("SMH") - p("XLP")) if p("SMH") is not None and p("XLP") is not None else None
    tones = eng.st.get("tone", [])
    f["tone_up"] = sum(1 for t in tones if t["v"] > 0)
    f["tone_down"] = sum(1 for t in tones if t["v"] < 0)
    return f

def my_read(eng):
    import econ_calendar
    return econ_calendar.hi_conf(eng.core, eng.st, econ_calendar.adjust_read(eng.core, _my_read(eng), "us"))


def _my_read(eng):
    f = facts(eng)
    idx = [v for v in (f.get("NQ"), f.get("ES"), f.get("YM")) if v is not None]
    if not idx:
        return {"bias": "NO CLEAR BIAS", "conf": "low", "why": ["prices not loaded yet"], "lead": ""}
    avg = sum(idx) / len(idx)
    score, why = 0, []
    if abs(avg) >= 0.4:
        score += 1 if avg > 0 else -1
        why.append(f"indexes {'up' if avg > 0 else 'down'} on average {avg:+.1f}%")
    v = f.get("vix")
    if v is not None and abs(v) >= 5:
        score += -1 if v > 0 else 1
        why.append(f"fear gauge (VIX) {'rising' if v > 0 else 'falling'} {v:+.0f}%")
    y = f.get("yield")
    if y is not None and abs(y) >= 1:
        score += -1 if y > 0 else 1
        why.append(f"10-year yield {'up' if y > 0 else 'down'} - {'pressure' if y > 0 else 'help'} for NQ most")
    n, up = f["sectors_n"], f["sectors_up"]
    if n:
        if up >= n * 0.7:
            score += 1; why.append(f"{up} of {n} sectors up - broad buying")
        elif up <= n * 0.3:
            score -= 1; why.append(f"only {up} of {n} sectors up - broad selling")
    r = f.get("risk_on")
    if r is not None and abs(r) >= 1.5:
        score += 1 if r > 0 else -1
        why.append("chips beating safe sectors - risk-on" if r > 0 else "safe sectors beating chips - risk-off")
    tu, td = f["tone_up"], f["tone_down"]
    if tu + td >= 4 and abs(tu - td) >= 3:
        score += 1 if tu > td else -1
        why.append(f"news mood: {tu} push-up vs {td} push-down headlines (3 hours)")
    if score >= 2:
        bias, conf = "UP", "medium" if score <= 3 else "high"
    elif score <= -2:
        bias, conf = "DOWN", "medium" if score >= -3 else "high"
    else:
        bias, conf = "NO CLEAR BIAS", "low"
    ranked = sorted(((k, f[k]) for k in ("NQ", "ES", "YM") if f.get(k) is not None), key=lambda x: -x[1])
    lead = f"Strongest {ranked[0][0]} {ranked[0][1]:+.1f}%  ·  weakest {ranked[-1][0]} {ranked[-1][1]:+.1f}%" if ranked else ""
    return {"bias": bias, "conf": conf, "why": why[:5 if conf == "high" else 3] or ["signals are mixed or quiet"], "lead": lead}

def mentor(eng):
    import econ_calendar
    note = econ_calendar.mentor_note(eng.core, "us")
    hi = econ_calendar.hi_note(my_read(eng), "us")
    out = [x for x in (hi, note) if x] + _mentor(eng)
    return out[:4 if hi else 3]


def _mentor(eng):
    c = eng.core
    f, now, out = facts(eng), c.ny_now(), []
    hm = now.strftime("%H:%M")
    nq, ym, v, y = f.get("NQ"), f.get("YM"), f.get("vix"), f.get("yield")
    if hm < "09:30":
        out.append("Before the open: if the overnight move holds into 9:30, it often runs the first hour - "
                   "if it fades by 9:45, the open usually becomes the high or low of the morning.")
    if nq is not None and ym is not None and abs(nq - ym) >= 1:
        lead, lag = ("NQ", "YM") if nq > ym else ("YM", "NQ")
        out.append(f"Split market: {lead} leading, {lag} lagging - if the leader stalls, "
                   f"watch whether the laggard catches up or the leader gives back.")
    if v is not None and nq is not None:
        if v < -5 and nq > 0:
            out.append("Fear is fading while prices rise - while VIX keeps falling, dips usually get bought.")
        elif v > 5 and nq > 0:
            out.append("Prices up but fear rising - if a bad headline hits, the drop can be fast.")
        elif v > 5 and nq < 0:
            out.append("Fear rising with prices falling - if VIX keeps climbing, bounces tend to be sold.")
    if y is not None and abs(y) >= 1.5:
        out.append("Yields moving hard - NQ feels it most; if yields reverse, tech usually reverses with them.")
    for e in getattr(eng.earnings, "items", [])[:3]:
        d = e["date"]
        if d == now.strftime("%Y-%m-%d") or d == (now + timedelta(days=1)).strftime("%Y-%m-%d"):
            out.append(f"{e['name']} reports {e['when']} - big players often wait, so NQ can drift before the number "
                       "and jump after it.")
            break
    if now.weekday() == 4 and hm >= "13:00":
        out.append("Friday afternoon: big players cut risk before the weekend - late moves can be sharp.")
    if hm >= "15:30":
        out.append("Into the close: at 15:50 big funds send their closing orders - the last 10 minutes can jump.")
    nxt = now + timedelta(days=3)
    if nxt.month != now.month and now.day >= 27:
        out.append("Month-end: pension funds rebalance - if stocks rose a lot this month, they may sell into the close.")
    if not out:
        out.append("Quiet tape: if no big news comes, indexes usually follow chips (NQ) and yields (all three).")
    return out[:3]
