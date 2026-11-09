"""
Cloud version of US / Oil / Metals Market Intel.
Run by GitHub every few minutes: one update of all three screens, written to site/ for the iPhone page.
Your PC is not needed.
"""
import json, time, traceback
from pathlib import Path

import intel_bot as c, oil_intel, metals_intel, phone_server

HERE = Path(__file__).parent
SITE = HERE / "site"


def safe(fn):
    try:
        fn()
    except Exception:
        traceback.print_exc()


def page():
    p = phone_server.PAGE
    swaps = [
        ('href="/manifest.json"', 'href="manifest.json"'),
        ('href="/apple-touch-icon.png"', 'href="apple-touch-icon.png"'),
        ("Can't reach your PC. Is US Market Intel open on the PC, and is this phone on your home Wi-Fi?",
         "Can't load the latest data - check your internet."),
        ('const r=await fetch("/api",{cache:"no-store"});if(!r.ok)throw 0;render(await r.json());$("off").hidden=true}',
         'const r=await fetch("data.json?t="+Date.now(),{cache:"no-store"});if(!r.ok)throw 0;const d=await r.json();'
         'render(d);const age=Math.round((Date.now()/1000-(d.updated_ts||0))/60);'
         'if(age>30){$("off").textContent="Data is "+age+" min old - the cloud update is running late.";'
         '$("off").hidden=false}else $("off").hidden=true}'),
        ("setInterval(()=>{if(!document.hidden)load()},3000)", "setInterval(()=>{if(!document.hidden)load()},60000)"),
    ]
    for a, b in swaps:
        assert p.count(a) == 1, a
        p = p.replace(a, b)
    return p


def main():
    cfg = c.load_config()
    eng = c.Engine(cfg)
    eng.web_alerts = eng.st.get("web", [])
    cache = eng.st.get("earn_cache") or {}
    if cache.get("items") is not None:
        eng.earnings.items, eng.earnings.t, eng.earnings.status = cache["items"], cache["t"], "ok"
    oil = oil_intel.OilEngine(c, cfg)
    metals = metals_intel.MetalsEngine(c, cfg)
    for e in (oil, metals):
        if e.st.get("seeded"):
            e.caught_up = True          # don't re-show older news on every run

    safe(lambda: eng.prices.refresh(force=True))
    safe(oil.refresh_prices)
    safe(metals.refresh_prices)
    safe(eng.earnings.refresh)
    for step in (eng.check_news, eng.check_alarms, eng.check_snapshots, eng.check_earnings, eng.check_followups,
                 oil.check_news, oil.check_alarms, oil.check_events,
                 metals.check_news, metals.check_alarms, metals.check_events):
        safe(step)

    stamp = f"Cloud · updated {c.ny_now():%H:%M} NY · refreshes about every 5-15 min"
    eng.status = oil.status = metals.status = stamp

    eng.st["web"] = eng.web_alerts[-150:]
    eng.st["earn_cache"] = {"items": eng.earnings.items, "t": eng.earnings.t}
    safe(lambda: c.save_state(eng.st))
    safe(oil.save)
    safe(metals.save)

    data = phone_server.snapshot(c, eng, oil, metals)
    data["updated_ts"] = time.time()
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(page(), encoding="utf-8")
    (SITE / "data.json").write_text(json.dumps(data, default=str), encoding="utf-8")
    man = dict(phone_server.MANIFEST, start_url="./",
               icons=[{"src": "icon.png", "sizes": "180x180", "type": "image/png"},
                      {"src": "icon512.png", "sizes": "512x512", "type": "image/png"}])
    (SITE / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    small = phone_server.make_icon(180)
    for name in ("icon.png", "apple-touch-icon.png", "apple-touch-icon-precomposed.png"):
        (SITE / name).write_bytes(small)
    (SITE / "icon512.png").write_bytes(phone_server.make_icon(512))
    us = data.get("us") or {}
    print("Done.", stamp, "| US tiles:", len(us.get("tiles", [])), "| alerts:", len(us.get("alerts", [])))


if __name__ == "__main__":
    main()
