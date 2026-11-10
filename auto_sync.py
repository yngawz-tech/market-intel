"""
Keeps the PC windows and the iPhone (cloud) version the same.
GitHub (yngawz-tech/market-intel) is the master copy - the cloud/iPhone runs from it.
Every few minutes the PC checks GitHub; if a file there is newer, the PC copy is replaced
(old one saved in backups_auto/) and the windows restart by themselves.
A file changed on the PC but not yet on GitHub is never overwritten - it is copied to
github_upload/ and noted in log.txt as "needs upload".
"""
import hashlib, json, shutil, time, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
REPO = "yngawz-tech/market-intel"
FILES = ["intel_bot.py", "oil_intel.py", "metals_intel.py", "econ_calendar.py", "us_opinion.py",
         "phone_server.py", "auto_sync.py"]
STATE = HERE / "sync_state.json"
UA = {"User-Agent": "market-intel-pc"}


def _get(url, timeout=15):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def _h(b):
    return hashlib.sha1(b).hexdigest()


def _load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"commit": "", "files": {}}


def check(log=print):
    """Returns True when PC files were replaced with newer ones from GitHub (restart needed)."""
    st = _load()
    commit = json.loads(_get(f"https://api.github.com/repos/{REPO}/commits/main"))["sha"]
    if commit == st.get("commit"):
        return False
    changed, upload = False, HERE / "github_upload"
    for name in FILES:
        try:
            remote = _get(f"https://raw.githubusercontent.com/{REPO}/{commit}/{name}")
        except Exception:
            continue                                   # not on GitHub yet
        p = HERE / name
        local = p.read_bytes() if p.exists() else b""
        last = st["files"].get(name)
        if _h(local) == _h(remote):
            st["files"][name] = _h(remote)
        elif local and _h(local) != last:              # edited on the PC, not on GitHub yet
            upload.mkdir(exist_ok=True)
            shutil.copy2(p, upload / name)
            log(f"[sync] {name}: PC version is newer - needs upload to GitHub (copied to github_upload)")
        else:                                          # GitHub is newer
            bk = HERE / "backups_auto"
            bk.mkdir(exist_ok=True)
            if local:
                shutil.copy2(p, bk / f"{name}.{time.strftime('%Y%m%d-%H%M')}")
            p.write_bytes(remote)
            if upload.exists():
                (upload / name).write_bytes(remote)
            st["files"][name] = _h(remote)
            changed = True
            log(f"[sync] {name}: updated from GitHub")
    st["commit"] = commit
    STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")
    return changed
