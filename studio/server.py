"""Halal Duff Studio — custom FastAPI backend + hand-built faceplate frontend (replaces the Gradio UI).
Same pipeline underneath (engine.convert / render / deep_match); this just gives full design control.
Run from studio/ with the venv python (macOS/Linux: ../.venv/bin/python server.py — on Windows use the venv's
Scripts python), then open http://127.0.0.1:7860. Or just use the double-click launcher for your OS.
"""
import os, sys, uuid, threading, subprocess, glob, traceback, json, shutil, time
from urllib.parse import quote, unquote, urlparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine, config as C
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

# Install the RunPod cost-safety signal handlers on the MAIN thread at startup. engine.py imports
# runpod_sep lazily inside a worker thread, where signal.signal() is a no-op — so a SIGTERM mid-
# separation would skip cleanup and leave a pod billing. Best-effort: no key / no SDK -> just skip.
try:
    import runpod_sep
    runpod_sep.install_signal_handlers()
except Exception:
    pass

app = FastAPI()
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
ORIGINS = {"http://127.0.0.1:7860", "http://localhost:7860"}   # same-origin allowlist (CSRF guard)
ALLOWED_EXT = {".m4a", ".mp4", ".jpg"}                          # only media may be served
JOBS = {}     # job_id -> {status, progress, desc, result?, error?}
STATE = {}    # last successful convert: stems, groove, params, name, closeness, match
_PL_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()   # single-flight guard (one job at a time — protects the global STATE)


@app.middleware("http")
async def _guard(request: Request, call_next):
    """CSRF guard: a malicious page the user visits could POST to this loopback server (the response
    is unreadable cross-origin, but the side effect runs). Block any cross-origin POST and require a
    JSON content-type so a 'simple request' text/plain body can't slip through without a preflight."""
    if request.method == "POST" and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin and origin not in ORIGINS:
            return JSONResponse({"error": "cross-origin request blocked"}, status_code=403)
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return JSONResponse({"error": "expected application/json"}, status_code=415)
    return await call_next(request)


@app.exception_handler(Exception)
async def _on_error(request: Request, exc: Exception):
    """Always answer with JSON {error} so the frontend's `.error` contract holds for every failure —
    a raw 500 text/plain body used to make `await r.json()` throw and freeze the UI at 'starting…'."""
    traceback.print_exc()
    return JSONResponse({"error": f"{type(exc).__name__}: {str(exc)[:200]}"}, status_code=500)


_DUR = {}
def _dur(path):
    if path in _DUR: return _DUR[path]
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nk=1:nw=1", path], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout.strip()
        s = float(out); _DUR[path] = f"{int(s // 60)}:{int(s % 60):02d}"
    except Exception:
        _DUR[path] = ""
    return _DUR[path]

def _v(path):
    """Cache-bust query from the file's mtime — a re-render gives the SAME filename a new URL, so the
    browser refetches instead of replaying the stale cached audio (the 'sounds the same after re-render'
    bug). Old versioned URLs stay cached (harmless), so seeking within a version is still instant."""
    try: return f"?v={int(os.path.getmtime(path))}"
    except Exception: return ""

def _library():
    items = []
    for p in sorted(glob.glob(os.path.join(C.MASTERS, "*.m4a")), key=str.lower):
        name = os.path.splitext(os.path.basename(p))[0]
        mp4 = os.path.join(C.MASTERS, name + ".mp4"); jpg = os.path.join(C.MASTERS, name + ".jpg")
        items.append({"name": name, "audio": "/media/" + quote(name + ".m4a") + _v(p),
                      "video": ("/media/" + quote(name + ".mp4") + _v(mp4)) if os.path.exists(mp4) else None,
                      "cover": ("/cover/" + quote(name + ".jpg") + _v(jpg)) if os.path.exists(jpg) else None,
                      "dur": _dur(p)})
    return items

def _payload(name, match, params):
    n = engine._san(name)
    mp4 = os.path.join(C.MASTERS, n + ".mp4"); jpg = os.path.join(C.MASTERS, n + ".jpg"); m4a = os.path.join(C.MASTERS, n + ".m4a")
    return {"name": n, "audio": "/media/" + quote(n + ".m4a") + _v(m4a),
            "video": ("/media/" + quote(n + ".mp4") + _v(mp4)) if os.path.exists(mp4) else None,
            "cover": ("/cover/" + quote(n + ".jpg") + _v(jpg)) if os.path.exists(jpg) else None,
            "dur": _dur(m4a) if os.path.exists(m4a) else "",
            "match": match or {}, "params": params or {}}

_KNOBS = ("sub_vs_lead", "duff_level", "sub_lp", "sub_follow", "duck", "bright_db", "width", "reverb")

def _save_params(name, params):
    """Persist the tuning knobs next to the stems so a later re-tune (even after a restart) recalls them."""
    try:
        work = os.path.join(C.WORK, engine._san(name))
        if os.path.isdir(work):
            with open(os.path.join(work, "params.json"), "w") as f:
                json.dump({k: params[k] for k in _KNOBS if k in params}, f)
    except Exception: pass

def _ensure_working(name):
    """Point STATE at `name`, reloading its cached stems from work/ if it isn't already the working song.
    This is what lets you re-tune ANY track you pick from the library, not just the last one converted.
    Raises a clear error (surfaced to the UI) when the original stems aren't on disk anymore."""
    name = (name or STATE.get("name") or "").strip()
    if not name:
        raise RuntimeError("play or convert a song first, then re-tune it")
    if STATE.get("name") == name and STATE.get("stems"):
        return
    clean = engine._san(name)
    work = os.path.join(C.WORK, clean)
    drums = os.path.join(work, "drums.wav"); vocals = os.path.join(work, "vocals.wav"); groove = os.path.join(work, "groove.wav")
    if not (os.path.exists(drums) and os.path.exists(vocals) and os.path.exists(groove)):
        raise RuntimeError("the original stems for this track aren't on disk anymore — reconvert it to re-tune")
    vwork = os.path.join(work, "video.mp4")
    stems = dict(work=work, source=os.path.join(work, "source.wav"), drums=drums, vocals=vocals,
                 other=None, video=(vwork if os.path.exists(vwork) else None))
    params = engine._dyn_paths(stems, groove, False)          # correct absolute paths for THIS machine
    try:                                                      # merge the last-saved knobs back on top
        params.update(json.load(open(os.path.join(work, "params.json"))))
    except Exception: pass
    STATE.update(stems=stems, groove=groove, params=params, name=name, closeness=None, match=None)

def _vdir(name): return os.path.join(C.MASTERS, "versions", engine._san(name))
def _set_current(vdir, v):
    try: open(os.path.join(vdir, "current"), "w").write(str(v))
    except Exception: pass
def _get_current(vdir, vers):
    try: return int(open(os.path.join(vdir, "current")).read().strip())
    except Exception: return max((v["v"] for v in vers), default=0)   # newest render is the master by default

def _knobs_of(params):
    return {k: round(float(params[k]), 2) for k in ("sub_vs_lead", "duff_level", "sub_lp", "sub_follow", "duck", "bright_db") if params.get(k) is not None}

def _snapshot_original(name):
    """Before the FIRST re-tune of a track that predates version history, preserve its current master
    as v1 'Original' so the user can always A/B back to what they started with. No-op if history exists."""
    try:
        vdir = _vdir(name)
        if os.path.exists(os.path.join(vdir, "versions.json")): return
        src = os.path.join(C.MASTERS, engine._san(name) + ".m4a")
        if not os.path.exists(src): return
        os.makedirs(vdir, exist_ok=True)
        shutil.copyfile(src, os.path.join(vdir, "v1.m4a"))
        knobs = {}
        try: knobs = _knobs_of(json.load(open(os.path.join(C.WORK, engine._san(name), "params.json"))))
        except Exception: pass
        with open(os.path.join(vdir, "versions.json"), "w") as f:
            json.dump([{"v": 1, "file": "v1.m4a", "knobs": knobs, "closeness": None,
                        "ts": int(os.path.getmtime(src)), "label": "original"}], f, indent=2)
    except Exception:
        traceback.print_exc()

def _save_version(name, label=None):
    """Snapshot the just-rendered master as a numbered version so tunings can be A/B compared later.
    Kept under Masters/versions/<song>/ (git-ignored). Original + last 15 renders are retained."""
    try:
        clean = engine._san(name)
        src = os.path.join(C.MASTERS, clean + ".m4a")
        if not os.path.exists(src): return
        vdir = _vdir(name); os.makedirs(vdir, exist_ok=True)
        idx = os.path.join(vdir, "versions.json")
        try: vers = json.load(open(idx))
        except Exception: vers = []
        n = max((v["v"] for v in vers), default=0) + 1
        shutil.copyfile(src, os.path.join(vdir, f"v{n}.m4a"))
        vers.append({"v": n, "file": f"v{n}.m4a", "knobs": _knobs_of(STATE.get("params") or {}),
                     "closeness": (STATE.get("match") or {}).get("closeness"), "ts": int(time.time()), "label": label})
        origs = [v for v in vers if v.get("label") == "original"][:1]      # never prune the original
        rest = [v for v in vers if v.get("label") != "original"][-15:]
        vers = origs + rest
        keep = {v["file"] for v in vers}
        for f in glob.glob(os.path.join(vdir, "v*.m4a")):
            if os.path.basename(f) not in keep:
                try: os.remove(f)
                except Exception: pass
        with open(idx, "w") as f: json.dump(vers, f, indent=2)
        _set_current(vdir, n)                                              # the render you just made is now current
    except Exception:
        traceback.print_exc()

def _job(fn):
    # SINGLE-FLIGHT: this app has ONE global STATE (the last convert's stems/params). Refuse to start a
    # second job while one is running — otherwise two converts (e.g. two browser tabs, where the
    # frontend busy-guard is per-tab) race STATE and a following rerender/match acts on the wrong song
    # or overwrites the wrong master. The lock makes the check-and-claim atomic.
    with _RUN_LOCK:
        if any(v.get("status") == "running" for v in JOBS.values()):
            return {"error": "a job is already running — let it finish first"}
        # prune finished jobs so JOBS doesn't grow unbounded over a long-running local session
        finished = [k for k, v in JOBS.items() if v.get("status") != "running"]
        for k in finished[:-20]: JOBS.pop(k, None)
        jid = uuid.uuid4().hex[:12]
        JOBS[jid] = {"status": "running", "progress": 0.0, "desc": "starting…"}
    def run():
        try:
            def cb(f, m):
                try: JOBS[jid].update(progress=float(f), desc=str(m))   # progress must never fail a job
                except Exception: pass
            JOBS[jid].update(status="done", progress=1.0, desc="done", result=fn(cb))
        except Exception as e:
            traceback.print_exc()
            JOBS[jid].update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}")
    threading.Thread(target=run, daemon=True).start()
    return {"job_id": jid}

@app.get("/")
def index(): return FileResponse(os.path.join(WEB, "index.html"), headers={"Cache-Control": "no-store, max-age=0"})

@app.get("/favicon.svg")
def favicon(): return FileResponse(os.path.join(WEB, "favicon.svg"), media_type="image/svg+xml")

@app.get("/manifest.json")
def manifest(): return FileResponse(os.path.join(WEB, "manifest.json"), media_type="application/manifest+json")

@app.get("/sw.js")
def sw(): return FileResponse(os.path.join(WEB, "sw.js"), media_type="application/javascript",
                             headers={"Cache-Control": "no-store"})     # scope=/, always fresh

@app.get("/logo.svg")
def logo(): return FileResponse(os.path.join(WEB, "logo.svg"), media_type="image/svg+xml")

@app.get("/icon-{size}.png")
def pwa_icon(size: str):
    f = os.path.join(WEB, f"icon-{os.path.basename(size)}.png")
    if not os.path.exists(f): return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(f, media_type="image/png")

@app.get("/api/library")
def library(): return JSONResponse(_library())

def _serve(fname):
    base = os.path.basename(unquote(fname))                              # basename blocks ../ traversal
    if os.path.splitext(base)[1].lower() not in ALLOWED_EXT:            # only media, never playlists.json etc.
        return JSONResponse({"error": "not found"}, status_code=404)
    path = os.path.join(C.MASTERS, base)
    if not os.path.exists(path): return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)

@app.get("/media/{fname}")
def media(fname: str): return _serve(fname)

@app.get("/cover/{fname}")
def cover(fname: str): return _serve(fname)

@app.get("/download/{fname}")
def download(fname: str):
    """Serve the track as a fresh MP3 (universal — attaches cleanly to WhatsApp, no m4a quirks).
    Transcoded once per render and cached (keyed by the master's mtime, so a re-render regenerates it)."""
    name = os.path.splitext(os.path.basename(unquote(fname)))[0]
    src = os.path.join(C.MASTERS, name + ".m4a")
    if not os.path.exists(src):
        return JSONResponse({"error": "not found"}, status_code=404)
    cache = os.path.join(C.MASTERS, ".dlcache"); os.makedirs(cache, exist_ok=True)
    mp3 = os.path.join(cache, f"{engine._san(name)}-{int(os.path.getmtime(src))}.mp3")
    if not os.path.exists(mp3):
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
                        "-c:a", "libmp3lame", "-q:a", "2", "-map_metadata", "0", mp3], check=True)
    return FileResponse(mp3, media_type="audio/mpeg", filename=name + ".mp3")

@app.get("/api/versions/{name}")
def versions(name: str):
    clean = engine._san(unquote(name))
    vdir = os.path.join(C.MASTERS, "versions", clean)
    try: vers = json.load(open(os.path.join(vdir, "versions.json")))
    except Exception: vers = []
    cur = _get_current(vdir, vers)
    for v in vers:
        v["audio"] = "/version/" + quote(clean) + "/" + quote(v["file"]) + _v(os.path.join(vdir, v["file"]))
        v["current"] = (v["v"] == cur)
    return JSONResponse(vers)

@app.post("/api/versions/{name}/use")
async def use_version(name: str, req: Request):
    """Make an older version the current master (so it plays everywhere + shows in the library)."""
    b = await req.json(); v = int(b.get("v", 0))
    clean = engine._san(unquote(name)); vdir = os.path.join(C.MASTERS, "versions", clean)
    src = os.path.join(vdir, f"v{v}.m4a")
    if not os.path.exists(src): return JSONResponse({"error": "version not found"}, status_code=404)
    shutil.copyfile(src, os.path.join(C.MASTERS, clean + ".m4a"))
    _set_current(vdir, v)
    return JSONResponse(_payload(clean, {}, {}))

@app.get("/version/{name}/{fname}")
def version_media(name: str, fname: str):
    clean = engine._san(unquote(name)); base = os.path.basename(unquote(fname))
    if os.path.splitext(base)[1].lower() != ".m4a":
        return JSONResponse({"error": "not found"}, status_code=404)
    path = os.path.join(C.MASTERS, "versions", clean, base)
    if not os.path.exists(path): return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)

PL = os.path.join(C.MASTERS, "playlists.json")
def _load_pl():
    try: return json.load(open(PL))
    except FileNotFoundError: return {}
    except Exception:
        print(f"[playlists] ⚠️  {PL} is unreadable/corrupt — starting empty", file=sys.stderr); return {}
def _save_pl(d):
    tmp = PL + ".tmp"                                                    # atomic write: no half-truncated file
    with open(tmp, "w") as f: json.dump(d, f, indent=2)
    os.replace(tmp, PL)

@app.get("/api/playlists")
def playlists(): return JSONResponse(_load_pl())

@app.post("/api/playlists")
async def playlists_edit(req: Request):
    b = await req.json(); act = b.get("action"); name = (b.get("name") or "").strip()[:80]; track = b.get("track")
    with _PL_LOCK:                                                       # serialize read-modify-write
        d = _load_pl()
        if act == "create" and name: d.setdefault(name, [])
        elif act == "delete" and name: d.pop(name, None)
        elif act == "add" and name in d and track and track not in d[name]: d[name].append(track)
        elif act == "remove" and name in d and track: d[name] = [t for t in d[name] if t != track]
        elif act == "rename" and name in d:
            new = (b.get("new") or "").strip()[:80]
            if new and new not in d: d[new] = d.pop(name)
        _save_pl(d)
    return JSONResponse(d)

@app.post("/api/track/rename")
async def track_rename(req: Request):
    b = await req.json()
    old = engine._san(b.get("name") or ""); new = engine._san(b.get("new") or "")
    if not old or not new: return JSONResponse({"error": "name required"}, status_code=400)
    if new == old: return JSONResponse({"ok": True, "name": new})
    if os.path.exists(os.path.join(C.MASTERS, new + ".m4a")):
        return JSONResponse({"error": f"a track named “{new}” already exists"}, status_code=400)
    moved = False
    for ext in (".m4a", ".mp4", ".jpg"):                                  # master, video, cover
        src = os.path.join(C.MASTERS, old + ext); dst = os.path.join(C.MASTERS, new + ext)
        if os.path.exists(src):
            os.replace(src, dst); moved = moved or ext == ".m4a"
    if not moved: return JSONResponse({"error": "track not found"}, status_code=404)
    for base in (os.path.join(C.MASTERS, "versions"), C.WORK):            # move version history + cached stems
        s = os.path.join(base, old); dd = os.path.join(base, new)
        if os.path.isdir(s) and not os.path.exists(dd):
            try: os.replace(s, dd)
            except Exception: pass
    with _PL_LOCK:                                                        # repoint playlist references
        d = _load_pl()
        for k in d: d[k] = [new if t == old else t for t in d[k]]
        _save_pl(d)
    if STATE.get("name") and engine._san(STATE["name"]) == old: STATE["name"] = new
    return JSONResponse({"ok": True, "name": new})

@app.post("/api/track/delete")
async def track_delete(req: Request):
    b = await req.json(); clean = engine._san(b.get("name") or "")
    if not clean: return JSONResponse({"error": "name required"}, status_code=400)
    for ext in (".m4a", ".mp4", ".jpg"):
        f = os.path.join(C.MASTERS, clean + ext)
        if os.path.exists(f):
            try: os.remove(f)
            except Exception: pass
    vd = os.path.join(C.MASTERS, "versions", clean)
    if os.path.isdir(vd): shutil.rmtree(vd, ignore_errors=True)
    with _PL_LOCK:                                                        # drop from every playlist
        d = _load_pl()
        for k in list(d): d[k] = [t for t in d[k] if t != clean]
        _save_pl(d)
    return JSONResponse({"ok": True})

@app.get("/api/config")
def get_config():
    kp = os.path.expanduser("~/.runpod_key")
    return JSONResponse({"has_key": bool(os.environ.get("RUNPOD_API_KEY")) or os.path.exists(kp)})

@app.post("/api/config")
async def set_config(req: Request):
    b = await req.json(); key = (b.get("runpod_key") or "").strip(); kp = os.path.expanduser("~/.runpod_key")
    saved = False
    if key:
        with open(kp, "w") as f: f.write(key)
        saved = True
        if os.name == "nt":                                          # chmod is a no-op on Windows; lock via icacls
            try: subprocess.run(["icacls", kp, "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME','')}:F"],
                                capture_output=True)
            except Exception: pass
        else:
            os.chmod(kp, 0o600)
    return JSONResponse({"saved": saved, "has_key": os.path.exists(kp)})

def _backfill_covers():
    # tracks with a video: grab a representative frame
    for p in glob.glob(os.path.join(C.MASTERS, "*.mp4")):
        name = os.path.splitext(os.path.basename(p))[0]; jpg = os.path.join(C.MASTERS, name + ".jpg")
        if not os.path.exists(jpg):
            try:
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "3", "-i", p,
                                "-frames:v", "1", "-vf", "scale=640:-1", jpg], check=True)
            except Exception: pass
    # audio-only tracks: try embedded cover art; if none, leave cover=None (UI shows a designed vinyl ring)
    for p in glob.glob(os.path.join(C.MASTERS, "*.m4a")):
        name = os.path.splitext(os.path.basename(p))[0]
        jpg = os.path.join(C.MASTERS, name + ".jpg"); mp4 = os.path.join(C.MASTERS, name + ".mp4")
        if os.path.exists(jpg) or os.path.exists(mp4): continue
        try:
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", p,
                            "-an", "-frames:v", "1", jpg], check=True)
            if not os.path.exists(jpg) or os.path.getsize(jpg) < 1200:   # no real embedded art
                if os.path.exists(jpg): os.remove(jpg)
        except Exception:
            if os.path.exists(jpg):
                try: os.remove(jpg)
                except Exception: pass

@app.post("/api/convert")
async def convert(req: Request):
    b = await req.json()
    url = (b.get("url") or "").strip(); name = (b.get("name") or "").strip()
    tune = bool(b.get("auto_tune", True)); gpu = bool(b.get("gpu", False))
    if not url: return JSONResponse({"error": "paste a link first"}, status_code=400)
    if urlparse(url).scheme not in ("http", "https"):
        return JSONResponse({"error": "only http/https links are supported"}, status_code=400)
    if gpu and not (os.environ.get("RUNPOD_API_KEY") or os.path.exists(os.path.expanduser("~/.runpod_key"))):
        return JSONResponse({"error": "gpu is on but no RunPod key set — add one in Settings"}, status_code=400)
    def fn(cb):
        nm = name or (engine.fetch_title(url) or "song")
        r = engine.convert(url, nm, want_pad=False, tune=tune, progress=cb, sep_mode=("runpod" if gpu else "local"))
        STATE.update(stems=r["stems"], groove=r["groove"], params=r["params"], name=nm,
                     closeness=r.get("closeness"), match=r.get("match"))
        _save_params(nm, r["params"]); _save_version(nm, label="original")   # v1 = the original convert
        return _payload(nm, r.get("match"), r["params"])
    return _job(fn)

@app.post("/api/rerender")
async def rerender(req: Request):
    b = await req.json()
    def fn(cb):
        _ensure_working(b.get("name"))                       # re-tune the track you picked; clear error if stems gone
        _snapshot_original(STATE["name"])                    # preserve the pre-tune master as v1 'Original'
        p = dict(STATE["params"])
        p.update(sub_vs_lead=float(b.get("heaviness", p.get("sub_vs_lead", 0.85))),
                 sub_lp=float(b.get("sub_depth", p.get("sub_lp", 95))),
                 sub_follow=float(b.get("beat_swell", p.get("sub_follow", 1.5))),
                 duck=float(b.get("vocal_duck", p.get("duck", 0.55))),
                 duff_level=float(b.get("duff_level", p.get("duff_level", 1.0))),
                 bright_db=float(b.get("brightness", 0.0)), pad_off=1)
        engine.render(STATE["stems"], STATE["groove"], p, STATE["name"], progress=cb)
        STATE["params"] = p
        _save_params(STATE["name"], p); _save_version(STATE["name"])
        return _payload(STATE["name"], STATE.get("match"), p)
    return _job(fn)

@app.post("/api/match")
async def match(req: Request):
    b = await req.json()
    def fn(cb):
        _ensure_working(b.get("name"))
        _snapshot_original(STATE["name"])                    # preserve the pre-match master as v1 'Original'
        r = engine.deep_match(STATE["stems"], progress=cb)
        STATE.update(groove=r["groove"], params=r["params"], closeness=r["closeness"], match=r.get("detail"))
        cb(0.9, "rendering the closer version…")
        engine.render(STATE["stems"], r["groove"], r["params"], STATE["name"], progress=cb)
        _save_params(STATE["name"], r["params"]); _save_version(STATE["name"])
        return _payload(STATE["name"], r.get("detail"), r["params"])
    return _job(fn)

@app.get("/api/status/{jid}")
def status(jid: str):
    j = JOBS.get(jid)
    return JSONResponse(j) if j else JSONResponse({"error": "unknown job"}, status_code=404)

def _seed_originals():
    """Guarantee every library track has a clearly-labeled 'Original' version so the version list always
    starts from a baseline. Only touches songs that DON'T already have an original-labeled entry (idempotent —
    it won't wipe real edit history once seeded). This also clears the confusing pre-feature test snapshots."""
    for p in glob.glob(os.path.join(C.MASTERS, "*.m4a")):
        name = os.path.splitext(os.path.basename(p))[0]
        vdir = _vdir(name)
        try:
            vers = json.load(open(os.path.join(vdir, "versions.json")))
            if any(v.get("label") == "original" for v in vers): continue    # already seeded — leave history alone
        except Exception: pass
        try:
            if os.path.isdir(vdir): shutil.rmtree(vdir)
            os.makedirs(vdir, exist_ok=True)
            shutil.copyfile(p, os.path.join(vdir, "v1.m4a"))
            knobs = {}
            try: knobs = _knobs_of(json.load(open(os.path.join(C.WORK, engine._san(name), "params.json"))))
            except Exception: pass
            with open(os.path.join(vdir, "versions.json"), "w") as f:
                json.dump([{"v": 1, "file": "v1.m4a", "knobs": knobs, "closeness": None,
                            "ts": int(os.path.getmtime(p)), "label": "original"}], f, indent=2)
            _set_current(vdir, 1)
        except Exception:
            traceback.print_exc()

if __name__ == "__main__":
    print("Halal Duff Studio -> http://127.0.0.1:7860")
    _backfill_covers()
    _seed_originals()
    uvicorn.run(app, host="127.0.0.1", port=7860, log_level="warning")
