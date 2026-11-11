"""
Phone page for US Market Intel + Oil Market Intel.
Started automatically by intel_bot.py. Shows the same live screens on your iPhone over home Wi-Fi.
Address: shown at the bottom of the PC window ("iPhone: http://...").
"""
import json, socket, struct, threading, zlib
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))          # sends nothing; just finds this PC's home-network address
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return socket.gethostname()
    finally:
        s.close()


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


# ================= data (same as the PC windows) =================
def us_part(c, eng):
    qs = eng.prices.get()
    pct = c.pct
    out = {"tiles": [{"name": n, "p": round(pct(qs[s]), 2)} for n, s in c.INDEXES.items() if s in qs],
           "macro": "   ".join(f"{n} {pct(qs[s]):+.1f}%" for n, s in c.MACRO_PRICES.items() if s in qs),
           "pins": sorted(list(eng.top_today()), key=lambda t: t["time"], reverse=True),
           "sectors": [{"name": n, "p": round(p, 2)} for n, p in c.sector_ranking(qs)],
           "alerts": list(reversed(list(getattr(eng, "web_alerts", []))))[:100],
           "status": eng.status}
    try:
        import us_opinion
        out["read"] = us_opinion.my_read(eng)
        out["mentor"] = us_opinion.mentor(eng)
    except Exception as e:
        out["read"] = {"bias": "NO CLEAR BIAS", "conf": "low", "lead": "",
                       "why": [f"read unavailable: {e.__class__.__name__}"]}
        out["mentor"] = []
    items = list(eng.earnings.items)
    out["earnings"] = [c.earnings_line(e) for e in items[:5]]
    out["earn_msg"] = ("" if items else "None of the big names report in the next 2 weeks"
                       if eng.earnings.status == "ok" else eng.earnings.status)
    return out


def oil_part(oil):
    import oil_intel as oi
    qs = oil.prices()
    pct = oil.c.pct
    return {"tiles": [{"name": n.replace(" crude", ""), "p": round(pct(qs[s]), 2), "price": f"{qs[s][0]:.2f}$"}
                      for n, s in oi.OIL_MAIN.items() if s in qs],
            "other": "   ".join(f"{n} {pct(qs[s]):+.1f}%" for n, s in oi.OIL_OTHER.items() if s in qs),
            "pins": sorted(list(oil.top_today()), key=lambda t: t["time"], reverse=True)[:3],
            "read": _safe(oil.my_read, {"bias": "NO CLEAR BIAS", "conf": "low", "why": ["read unavailable"]}),
            "mentor": _safe(oil.mentor, []),
            "plumbing": [list(r) for r in _safe(lambda: oil.plumbing(qs), [])],
            "tankers": [{"name": n, "p": round(pct(qs[s]), 2) if s in qs else None} for n, s in oi.TANKERS.items()],
            "calendar": _safe(lambda: [f"{t}  ·  {w}" for _, t, w in oil.calendar()[:4]], []),
            "news": [{"time": a["time"], "kind": a["kind"], "src": a["lines"][-1] if a.get("lines") else "",
                      "title": a["title"], "link": a.get("link", "")} for a in _safe(lambda: oil.best_news(3), [])],
            "status": oil.status}


def metals_part(m):
    import metals_intel as mi
    qs = m.prices()
    pct = m.c.pct
    return {"tiles": [{"name": n, "p": round(pct(qs[s]), 2), "price": f"{qs[s][0]:,.2f}$"}
                      for n, s in mi.METAL_MAIN.items() if s in qs],
            "other": "   ".join(f"{n} {pct(qs[s]):+.1f}%" for n, s in mi.METAL_OTHER.items() if s in qs),
            "pins": sorted(list(m.top_today()), key=lambda t: t["time"], reverse=True)[:3],
            "read": _safe(m.my_read, {"bias": "NO CLEAR BIAS", "conf": "low", "why": ["read unavailable"]}),
            "mentor": _safe(m.mentor, []),
            "plumbing": [list(r) for r in _safe(lambda: m.plumbing(qs), [])],
            "stocks": [{"name": n, "p": round(pct(qs[s]), 2) if s in qs else None} for n, s in mi.METAL_STOCKS.items()],
            "calendar": _safe(lambda: [f"{t}  ·  {w}" for _, t, w in m.calendar()[:4]], []),
            "news": [{"time": a["time"], "kind": a["kind"], "src": a["lines"][-1] if a.get("lines") else "",
                      "title": a["title"], "link": a.get("link", "")} for a in _safe(lambda: m.best_news(3), [])],
            "status": m.status}


def econ_part(c):
    import econ_calendar
    cal = econ_calendar.get(c)
    return {m: cal.banner(m) for m in ("us", "oil", "metals")}


def snapshot(c, eng, oil, metals=None):
    return {"now": c.ny_now().strftime("%H:%M:%S"),
            "econ": _safe(lambda: econ_part(c), None),
            "us": _safe(lambda: us_part(c, eng), None),
            "oil": _safe(lambda: oil_part(oil), None) if oil is not None else None,
            "metals": _safe(lambda: metals_part(metals), None) if metals is not None else None}


# ================= home-screen icon (drawn here, no files needed) =================
CANDLES = [  # body x0, x1, y0, y1, wick y0, y1, colour   (on a 180 grid)
    (30, 56, 88, 128, 76, 140, (120, 120, 120)),
    (77, 103, 64, 112, 50, 124, (212, 167, 44)),
    (124, 150, 36, 92, 24, 104, (212, 167, 44)),
]


def make_icon(size=180):
    k, bg = size / 180, (22, 22, 22)
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")
        Y = y / k
        for x in range(size):
            X, colr = x / k, bg
            for x0, x1, y0, y1, w0, w1, cc in CANDLES:
                mid = (x0 + x1) / 2
                if (x0 <= X < x1 and y0 <= Y < y1) or (mid - 2 <= X < mid + 2 and w0 <= Y < w1):
                    colr = cc
                    break
            row += bytes(colr)
        rows.append(bytes(row))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


MANIFEST = {"name": "Market Intel", "short_name": "Market Intel", "start_url": "/", "display": "standalone",
            "background_color": "#161616", "theme_color": "#161616",
            "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png"},
                      {"src": "/icon512.png", "sizes": "512x512", "type": "image/png"}]}


# ================= server =================
def start(core, eng, oil, port=8765, metals=None):
    icons = {}

    def icon(n):
        if n not in icons:
            icons[n] = make_icon(n)
        return icons[n]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, body, ctype, cache="no-store"):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            p = self.path.split("?")[0]
            try:
                if p in ("/", "/index.html"):
                    self.send(PAGE, "text/html; charset=utf-8")
                elif p == "/api":
                    self.send(json.dumps(snapshot(core, eng, oil, metals), default=str), "application/json")
                elif p == "/manifest.json":
                    self.send(json.dumps(MANIFEST), "application/manifest+json")
                elif p in ("/icon.png", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/favicon.ico"):
                    self.send(icon(180), "image/png", "max-age=86400")
                elif p == "/icon512.png":
                    self.send(icon(512), "image/png", "max-age=86400")
                else:
                    self.send_error(404)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://{lan_ip()}:{port}"


# ================= the phone page =================
PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Market Intel">
<meta name="theme-color" content="#161616">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<title>Market Intel</title>
<style>
:root{--bg:#161616;--text:#DADADA;--grey:#8A8A8A;--line:#2E2E2E;--gold:#D4A72C;--down:#9A9A9A;--soft:#AAAAAA;--pin:#221D10}
html,body{background:var(--bg);color:var(--text);margin:0;font:14px -apple-system,"Segoe UI",system-ui,sans-serif;-webkit-text-size-adjust:100%}
.wrap{padding:calc(env(safe-area-inset-top) + 10px) 14px calc(env(safe-area-inset-bottom) + 16px)}
.tiles{display:flex;gap:6px}
.tile{flex:1;border:1px solid var(--line);text-align:center;padding:5px 2px}
.tile .n{color:var(--grey);font-size:11px}
.tile .v{font-size:20px;font-weight:700}.tile .v.sm{font-size:14px}
.tile .pr{color:var(--grey);font-size:10px}
.small{color:var(--grey);font-size:11px;margin:5px 3px 0;line-height:1.5}
.hrow{display:flex;flex-wrap:wrap;gap:6px;justify-content:space-between;align-items:center;margin:12px 3px 4px}
.hrow b{color:var(--gold);font-size:14px}
.sw{background:var(--pin);color:var(--gold);border:1px solid var(--gold);font:700 13px -apple-system,"Segoe UI",sans-serif;padding:6px 14px;border-radius:0;-webkit-appearance:none}
.pin{background:var(--pin);border:1px solid var(--gold);padding:6px 8px}
.pt{font-weight:700;font-size:13px;margin-top:2px}.ps{color:#9A9A9A;font-size:12px;margin-bottom:4px}
.box{border:1px solid var(--line);padding:5px 9px 7px;margin-top:7px}
.bh{font-weight:700;font-size:13px}.bh.hi{font-size:14px;letter-spacing:-.15px}.bh.hi .lb{font-size:13px}.tm{font-size:10px;font-weight:400;color:var(--soft);margin-left:5px}.bb{color:var(--soft);font-size:12px;line-height:1.45;margin-top:2px}
.lab{color:var(--grey);font-size:13px;margin:14px 3px 4px}
.bar{display:flex;align-items:center;gap:8px;font-size:13px;padding:2px 3px}
.bn{flex:0 0 44%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bt{flex:1;position:relative;height:10px}
.mid{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--line)}
.fill{position:absolute;top:2px;bottom:2px}
.bp{flex:0 0 52px;text-align:right;font-weight:700}
.txt{font-size:13px;line-height:1.55;padding:0 3px}
.feed{border:1px solid var(--line);padding:2px 8px}
.al{padding:8px 0;border-bottom:1px solid var(--line)}.al:last-child{border-bottom:0}
.ak{color:var(--grey);font-size:11px;font-weight:700}
.at{font-weight:700;font-size:14px;margin:2px 0;display:block;color:var(--text);text-decoration:none}
.ln{color:var(--soft);font-size:12px}.lk{color:var(--grey);font-size:12px}
.pl{display:flex;gap:8px;font-size:12px;padding:2px 3px}.pk{flex:0 0 34%;font-weight:700}.pv{color:var(--soft)}
.status{color:var(--grey);font-size:11px;margin:10px 3px 0}
.econ{background:var(--pin);border:1px solid var(--gold);padding:6px 9px;margin-top:8px}
.eh{color:var(--gold);font-weight:700;font-size:13px;margin-top:2px}.eh.done{opacity:.5}
.eh i{font-style:normal;color:var(--text);font-weight:400;font-size:12px}
.ed{color:var(--soft);font-size:12px;line-height:1.45;margin-top:3px}
.off{position:sticky;top:0;background:#3a2a10;color:var(--gold);font-size:13px;padding:calc(env(safe-area-inset-top) + 8px) 14px 8px;z-index:5}
[hidden]{display:none!important}
</style></head><body>
<div id="off" class="off" hidden>Can't reach your PC. Is US Market Intel open on the PC, and is this phone on your home Wi-Fi?</div>
<div class="wrap">
<section id="us">
 <div class="tiles" id="us-tiles"></div>
 <div class="small" id="us-macro"></div>
 <div class="econ" id="us-econ">TODAY loading news calendar...</div>
 <div class="hrow"><b>Top market drivers today</b><span><button class="sw" onclick="show('oil')">OIL &#9656;</button> <button class="sw" onclick="show('metals')">METALS &#9656;</button></span></div>
 <div class="pin" id="us-pins"></div>
 <div class="box" id="us-read"></div>
 <div class="box"><div class="bh">Mentor's desk note (if this, then that)</div><div class="bb" id="us-mentor"></div></div>
 <div class="lab">Sectors vs yesterday's close</div>
 <div id="us-sectors"></div>
 <div class="lab">Big-name earnings (next 2 weeks)</div>
 <div class="txt" id="us-earn">loading...</div>
 <div class="lab">Alerts (newest on top)</div>
 <div class="feed" id="us-alerts"></div>
 <div class="status" id="us-status"></div>
</section>
<section id="oil" hidden>
 <div class="tiles" id="oil-tiles"></div>
 <div class="small" id="oil-other"></div>
 <div class="econ" id="oil-econ"></div>
 <div class="hrow"><b>Top oil drivers today</b><span><button class="sw" onclick="show('metals')">METALS &#9656;</button> <button class="sw" onclick="show('us')">&#9666; US</button></span></div>
 <div class="pin" id="oil-pins"></div>
 <div class="box" id="oil-read"></div>
 <div class="box"><div class="bh">Mentor's desk note (if this, then that)</div><div class="bb" id="oil-mentor"></div></div>
 <div class="lab">Oil plumbing (how the market is wired today)</div>
 <div id="oil-plumb"></div>
 <div class="lab">Tanker watch</div>
 <div id="oil-tank"></div>
 <div class="lab">Oil calendar (New York time)</div>
 <div class="txt" id="oil-cal"></div>
 <div class="lab">Most relevant oil news (top 3)</div>
 <div class="feed" id="oil-news"></div>
 <div class="status" id="oil-status"></div>
</section>
<section id="metals" hidden>
 <div class="tiles" id="met-tiles"></div>
 <div class="small" id="met-other"></div>
 <div class="econ" id="met-econ"></div>
 <div class="hrow"><b>Top metals drivers today</b><span><button class="sw" onclick="show('oil')">OIL &#9656;</button> <button class="sw" onclick="show('us')">&#9666; US</button></span></div>
 <div class="pin" id="met-pins"></div>
 <div class="box" id="met-read"></div>
 <div class="box"><div class="bh">Mentor's desk note (if this, then that)</div><div class="bb" id="met-mentor"></div></div>
 <div class="lab">Metals plumbing (how the market is wired today)</div>
 <div id="met-plumb"></div>
 <div class="lab">Metal stocks and funds vs yesterday's close</div>
 <div id="met-stocks"></div>
 <div class="lab">Metals calendar (New York time)</div>
 <div class="txt" id="met-cal"></div>
 <div class="lab">Most relevant metals news (top 3)</div>
 <div class="feed" id="met-news"></div>
 <div class="status" id="met-status"></div>
</section>
</div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]);
const col=p=>p>0?"var(--gold)":"var(--down)";
const sgn=(p,d)=>(p>0?"+":"")+Number(p).toFixed(d);
const okUrl=u=>/^https?:\/\//i.test(u||"")?u:"";
const cache={};
function put(id,html){if(cache[id]!==html){cache[id]=html;$(id).innerHTML=html}}
let view="us";
try{view=localStorage.getItem("mi-view")||"us"}catch(e){}
if(location.hash==="#oil")view="oil";if(location.hash==="#metals")view="metals";if(!["us","oil","metals"].includes(view))view="us";
function show(v){view=v;try{localStorage.setItem("mi-view",v)}catch(e){}$("us").hidden=v!=="us";$("oil").hidden=v!=="oil";$("metals").hidden=v!=="metals";window.scrollTo(0,0)}
show(view);
function tiles(list,big){return (list||[]).map(t=>`<div class="tile"><div class="n">${esc(t.name)}</div><div class="v ${big?"":"sm"}" style="color:${col(t.p)}">${t.p>0?"&#9650;":"&#9660;"} ${sgn(t.p,2)}%</div>${t.price?`<div class="pr">${esc(t.price)}</div>`:""}</div>`).join("")||'<div class="small">prices loading...</div>'}
function pins(list,empty,sub){if(!list||!list.length)return `<div class="ps">${empty}</div>`;return list.map(t=>`<div class="pt">${esc(t.time)} NY&nbsp; ${esc(t.title)}</div>`+(sub?`<div class="ps">${esc(t.kind)} · ${esc(t.line)}</div>`:"")).join("")}
function age(t){return Math.max(1,Math.floor((Date.now()/1000-t)/60))+" min"}
function read(r){if(!r)return "";const a={UP:"&#9650;",DOWN:"&#9660;"}[r.bias]||"&#9632;";const c=r.bias==="UP"?"var(--gold)":r.bias==="DOWN"?"var(--down)":"var(--text)";const hi=!!r.hi_since;return `<div class="bh${hi?" hi":""}" style="color:${c}"><span class="lb">My read:</span> ${a} ${esc(r.bias)} (${hi?"HIGH":esc(r.conf)} confidence)${hi?`<span class="tm"><span data-since="${Number(r.hi_since)}">${age(r.hi_since)}</span> &middot; since ${esc(r.hi_at)}</span>`:""}</div><div class="bb">${r.lead?esc(r.lead)+"<br>":""}${(r.why||[]).map(w=>"• "+esc(w)).join("<br>")}</div>`}
setInterval(()=>document.querySelectorAll("[data-since]").forEach(e=>e.textContent=age(+e.dataset.since)),1000);
function bullets(l){return (l||[]).map(m=>"• "+esc(m)).join("<br>")}
function bars(list,minBig){list=list||[];const big=Math.max(minBig,...list.filter(x=>x.p!=null).map(x=>Math.abs(x.p)));return list.map(x=>{if(x.p==null)return `<div class="bar"><span class="bn">${esc(x.name)}</span><span class="bt"><i class="mid"></i></span><span class="bp" style="color:var(--grey)">--</span></div>`;const w=Math.abs(x.p)/big*50;const st=x.p>0?`left:50%;width:${w}%;background:var(--gold)`:`right:50%;width:${w}%;background:var(--down)`;return `<div class="bar"><span class="bn">${esc(x.name)}</span><span class="bt"><i class="mid"></i><i class="fill" style="${st}"></i></span><span class="bp" style="color:${col(x.p)}">${sgn(x.p,1)}%</span></div>`}).join("")}
function alerts(list){if(!list||!list.length)return '<div class="ps" style="padding:6px 0">No alerts yet since the PC app started.</div>';return list.map(a=>{const u=okUrl(a.link);return `<div class="al"><div class="ak">${esc(a.time)} NY&nbsp; · &nbsp;${esc(a.kind)}</div><div class="at">${esc(a.title)}</div>${(a.lines||[]).map(l=>`<div class="ln">${esc(l)}</div>`).join("")}${u?`<a class="lk" href="${esc(u)}" target="_blank" rel="noopener">open article</a>`:""}</div>`}).join("")}
function news(list,word){if(!list||!list.length)return `<div class="ps" style="padding:6px 0">No important ${word||"oil"} news yet today.</div>`;return list.map(a=>{const u=okUrl(a.link),t=esc(a.title);return `<div class="al"><div class="ak">${esc(a.time)} NY · ${esc(a.kind)} · ${esc(a.src)}</div>${u?`<a class="at" href="${esc(u)}" target="_blank" rel="noopener">${t}</a>`:`<div class="at">${t}</div>`}</div>`}).join("")}
function econ(b){if(!b)return '<div class="ed">news calendar unavailable</div>';let h=(b.today&&b.today.length)?b.today.map(l=>`<div class="eh${l.done?" done":""}">TODAY&nbsp; ${esc(l.text)}${l.cd?` <i>· ${esc(l.cd)}</i>`:""}</div>`).join(""):'<div class="eh">TODAY&nbsp; no big US news</div>';const det=(b.today||[]).filter(l=>l.detail&&!l.done).slice(0,2).map(l=>esc(l.detail));if(b.why)det.push(esc(b.why));det.push("This week: "+esc(b.week));if(b.status&&b.status!=="ok")det.push(esc(b.status));return h+`<div class="ed">${det.join("<br>")}</div>`}
function render(d){
 const E=d.econ||{};put("us-econ",econ(E.us));put("oil-econ",econ(E.oil));put("met-econ",econ(E.metals));
 const u=d.us;
 if(u){put("us-tiles",tiles(u.tiles,true));put("us-macro",esc(u.macro));
  put("us-pins",pins(u.pins,"Nothing big yet today. Market-leading news and big sector moves appear here.",true));
  put("us-read",read(u.read));put("us-mentor",bullets(u.mentor));put("us-sectors",bars(u.sectors,1));
  put("us-earn",u.earnings&&u.earnings.length?u.earnings.map(esc).join("<br>"):esc(u.earn_msg||"loading..."));
  put("us-alerts",alerts(u.alerts));put("us-status",esc(u.status)+"&nbsp;&nbsp;·&nbsp;&nbsp;prices: Yahoo (may be delayed)")}
 const o=d.oil;
 if(o){put("oil-tiles",tiles(o.tiles,false));put("oil-other",esc(o.other));
  put("oil-pins",pins(o.pins,"Nothing big yet. Supply shocks, OPEC, stockpile surprises and big moves appear here.",false));
  put("oil-read",read(o.read));put("oil-mentor",bullets(o.mentor));
  put("oil-plumb",(o.plumbing||[]).map(r=>`<div class="pl"><span class="pk">${esc(r[0])}</span><span class="pv">${esc(r[1])}</span></div>`).join(""));
  put("oil-tank",bars(o.tankers,2));put("oil-cal",(o.calendar||[]).map(esc).join("<br>"));
  put("oil-news",news(o.news));put("oil-status",esc(o.status)+"&nbsp;&nbsp;·&nbsp;&nbsp;tap a headline to open it")}
 else put("oil-status","Oil panel not available on the PC");
 const m=d.metals;
 if(m){put("met-tiles",tiles(m.tiles,false));put("met-other",esc(m.other));
  put("met-pins",pins(m.pins,"Nothing big yet. Fed, dollar, war headlines and big gold or silver moves appear here.",false));
  put("met-read",read(m.read));put("met-mentor",bullets(m.mentor));
  put("met-plumb",(m.plumbing||[]).map(r=>`<div class="pl"><span class="pk">${esc(r[0])}</span><span class="pv">${esc(r[1])}</span></div>`).join(""));
  put("met-stocks",bars(m.stocks,2));put("met-cal",(m.calendar||[]).map(esc).join("<br>"));
  put("met-news",news(m.news,"metals"));put("met-status",esc(m.status)+"&nbsp;&nbsp;·&nbsp;&nbsp;tap a headline to open it")}
 else put("met-status","Metals panel not available on the PC")}
async function load(){try{const r=await fetch("/api",{cache:"no-store"});if(!r.ok)throw 0;render(await r.json());$("off").hidden=true}catch(e){$("off").hidden=false}}
load();setInterval(()=>{if(!document.hidden)load()},3000);
document.addEventListener("visibilitychange",()=>{if(!document.hidden)load()});
</script></body></html>"""
