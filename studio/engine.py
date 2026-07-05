"""Halal Duff Studio — orchestrator. One call turns a song (URL or file) into a voice+duff
halal version + (if a video exists) a music-video mux.

Flow:  prepare (download + stems + vocal) -> groove (real duff) -> autotune (headless parallel
search, the ex-agent-workflow) -> render (dynamic level-match + master) -> mux video.
Halal boundary: human voice + frame drum only. No melodic instrument is ever synthesized here.
"""
import os, re, sys, json, glob, shutil, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C

def _san(name):
    s = re.sub(r'^\d+[_\-\s]*', '', (name or "song").strip())
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '', s).replace('_', ' ').strip()
    if s.strip('. ') == '':          # reject '.', '..', dots/spaces only — would escape the work dir
        s = 'song'
    return s or "song"

def _run(cmd, desc=""):
    try:
        # UTF-8 decode of tool output — yt-dlp/ffmpeg emit UTF-8 (song titles, paths); the OS locale codec
        # (cp1252 on Windows) would mojibake or CRASH on non-ASCII titles. errors='replace' never crashes.
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        tool = os.path.basename(str(cmd[0]))
        hint = ("winget install Gyan.FFmpeg yt-dlp.yt-dlp" if os.name == "nt" else
                "brew install ffmpeg yt-dlp" if sys.platform == "darwin" else
                "apt install ffmpeg  &&  pip install yt-dlp")
        raise RuntimeError(f"'{tool}' not found on PATH — install ffmpeg + yt-dlp, then reopen your terminal ({hint})")
    if p.returncode != 0:
        raise RuntimeError(f"{desc or cmd[0]} failed (rc={p.returncode}):\n{(p.stderr or p.stdout)[-1500:]}")
    return p.stdout

def _prog(cb, f, msg):
    if cb: cb(f, msg)

def _valid_stem(p):
    """True only if the stem exists AND is non-trivial + roughly song-length — so a partial/corrupt
    file left by an interrupted prior run is treated as absent (re-separated) rather than reused."""
    try:
        if not (os.path.exists(p) and os.path.getsize(p) > 100_000): return False
        import soundfile as sf
        return sf.info(p).duration > 5
    except Exception:
        return False

# ---------------- stages ----------------
def prepare(source, name, want_pad=False, progress=None, sep_mode=None):
    """Download (if URL) + extract stems. Reuses anything already present (safe to re-run)."""
    if want_pad:                                           # halal boundary is structural: voice + duff only
        raise RuntimeError("pad/instrument path is retired — Halal Duff Studio is voice + duff only")
    work = os.path.join(C.WORK, _san(name)); os.makedirs(work, exist_ok=True)
    src   = os.path.join(work, "source.wav"); video = os.path.join(work, "video.mp4")
    drums = os.path.join(work, "drums.wav"); other = os.path.join(work, "other.wav")
    vocals = os.path.join(work, "vocals.wav")
    # drop any partial/corrupt stem from an interrupted prior run so the gates below re-separate it
    for _stem in (drums, vocals, other):
        if os.path.exists(_stem) and not _valid_stem(_stem):
            try: os.remove(_stem)
            except Exception: pass
    is_url = bool(re.match(r'https?://', str(source)))
    # 1) audio + video
    if os.path.exists(src):
        _prog(progress, 0.10, "Reusing downloaded audio…")
        if not os.path.exists(video): video = None
    elif is_url:
        _prog(progress, 0.05, "Fetching audio…")
        _run([C.YTDLP, "--no-update", "--no-playlist", "-f", "bestaudio", "-x",
              "--audio-format", "wav", "-o", src, source], "yt-dlp audio")
        _prog(progress, 0.15, "Fetching video…")
        try:
            _run([C.YTDLP, "--no-update", "--no-playlist", "-f",
                  "bv*[ext=mp4]/bv*/b[ext=mp4]", "-o", video, source], "yt-dlp video")
        except Exception:
            video = None                                  # audio-only song; fine
        try:                                              # the original song's thumbnail -> library cover art
            _run([C.YTDLP, "--no-update", "--no-playlist", "--skip-download", "--write-thumbnail",
                  "--convert-thumbnails", "jpg", "-o", os.path.join(work, "cover"), source], "thumb")
        except Exception:
            pass
    else:
        shutil.copyfile(source, src)
        video = source if str(source).lower().endswith((".mp4", ".mkv", ".webm", ".mov")) else None
    # 1b) GPU offload (RunPod): both stems separated remotely, then the local steps below are skipped
    if (sep_mode or C.SEP_MODE) == "runpod" and not (os.path.exists(drums) and os.path.exists(vocals)):
        _prog(progress, 0.25, "GPU separation on RunPod (beat + vocal)…")
        import runpod_sep
        runpod_sep.separate(src, work,                     # writes work/drums.wav + work/vocals.wav
                            progress=(lambda f, m: _prog(progress, f, m)) if progress else None)
    # 2) beat (GPU demucs)
    if not os.path.exists(drums):
        _prog(progress, 0.25, "Separating the beat (GPU)…")
        _run([C.PY_BASE, os.path.join(C.ACA, "extract_drums_one.py"), src, drums, "drums"], "demucs drums")
    if want_pad and not os.path.exists(other):
        _run([C.PY_BASE, os.path.join(C.ACA, "extract_drums_one.py"), src, other, "other"], "demucs other")
    # 3) lead vocal (roformer via the Python API wrapper — ~10 min on CPU)
    if not os.path.exists(vocals):
        _first = not glob.glob(os.path.join(C.MODELS, "*.ckpt"))   # model cached in our own models/ dir (cross-platform)
        _prog(progress, 0.45, "Isolating the vocal — the first run also downloads the model (~640 MB, one time)…" if _first
                              else "Isolating the vocal (roformer, ~10-25 min on CPU — faster with RunPod GPU)…")
        sep = os.path.join(work, "sep"); os.makedirs(sep, exist_ok=True)
        out = _run([C.PY_SOTA, os.path.join(C.STUDIO, "separate_vocal.py"), src, sep, C.ROFORMER], "roformer")
        vpath = next((ln[len("VOCALS="):].strip() for ln in out.splitlines() if ln.startswith("VOCALS=")), "")
        if not vpath or not os.path.exists(vpath):
            hits = glob.glob(os.path.join(sep, "*ocal*.wav")); vpath = hits[0] if hits else ""
        if not vpath: raise RuntimeError("roformer produced no vocals stem")
        shutil.copyfile(vpath, vocals); shutil.rmtree(sep, ignore_errors=True)
    return dict(work=work, source=src, video=(video if video and os.path.exists(video) else None),
                drums=drums, other=(other if want_pad else None), vocals=vocals)

def build_groove(stems, groove_params=None, progress=None):
    """Real-frame-drum groove on the song's actual onsets (groove_real)."""
    _prog(progress, 0.60, "Building the duff groove…")
    work = stems["work"]; out = os.path.join(work, "groove.wav")
    P = dict(src=stems["source"], drums=stems["drums"], out=out)
    if groove_params: P.update(groove_params)
    pj = os.path.join(work, "groove_params.json"); json.dump(P, open(pj, "w"))
    _run([C.PY_SOTA, os.path.join(C.ACA, "groove_real.py"), pj, "g"], "groove_real")
    return out

def _dyn_paths(stems, groove, use_pad):
    p = dict(lead=stems["vocals"], drums=stems["drums"], duff=groove, ir=C.IR)
    if use_pad and stems.get("other"): p.update(other=stems["other"], pad_raw=stems.get("pad_raw"))
    else: p["pad_off"] = 1
    return p

def autotune(stems, groove, use_pad=False, iters=40, seeds=(11, 23, 37, 52), progress=None):
    """Headless parallel parameter search (the ex-agent-workflow). Returns the best params dict."""
    _prog(progress, 0.68, "Auto-tuning the balance…")
    base = _dyn_paths(stems, groove, use_pad)
    pj = os.path.join(stems["work"], "dyn_base.json"); json.dump(base, open(pj, "w"))
    dyn = os.path.join(C.STUDIO, "dyn.py")
    procs = [subprocess.Popen([C.PY_SOTA, dyn, "search", pj, str(s), str(iters)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for s in seeds]
    res = []
    for p in procs:
        out, _ = p.communicate()
        try: res.append(json.loads(out.strip().splitlines()[-1]))
        except Exception: pass
    if not res: return base
    best = max(res, key=lambda r: r["metrics"]["obj"])
    base.update(best["params"]); return base

def render(stems, groove, params, name, progress=None):
    """Dynamic level-match -> master (glue+loudnorm -14+limiter) -> mux video. Writes to Masters."""
    _prog(progress, 0.82, "Rendering the final mix…")
    work = stems["work"]; clean = _san(name)
    pj = os.path.join(work, "dyn_render.json"); json.dump(params, open(pj, "w"))
    mix = os.path.join(work, "mix.wav")
    _run([C.PY_SOTA, os.path.join(C.STUDIO, "dyn.py"), "render", pj, mix], "dyn render")
    _prog(progress, 0.90, "Mastering…")
    m4a = os.path.join(C.MASTERS, f"{clean}.m4a")
    bright = float(params.get("bright_db", 0) or 0)       # feedback: master air/brightness tweak
    treble = f"treble=g={bright}:f=8500:width_type=q:width=0.8," if abs(bright) > 0.1 else ""
    width  = float(params.get("width", 1.18))             # stereo width (the "surround" feel); 1.0 = none
    rev    = float(params.get("reverb", 0.12))            # convolution-reverb send (spatial depth/immersion)
    # SPATIAL MASTER: the voice+duff mix is dead-center; a stereo-IR convolution reverb decorrelates L/R
    # (width + depth) and stereotools widens it -> the immersive "surround" feel the old masters had.
    fc = (f"[0:a]{treble}asplit=2[dry][rv];"
          f"[1:a]aresample=48000[ir];"
          f"[rv][ir]afir=dry=0:wet=1:gtype=-1:irnorm=-1:length=1[wet];"
          f"[dry][wet]amix=inputs=2:weights=1 {rev}:normalize=0:dropout_transition=0[rvb];"
          f"[rvb]equalizer=f=180:t=q:w=1.2:g=1.0,equalizer=f=3300:t=q:w=2.0:g=1.0,"
          f"equalizer=f=6200:t=q:w=2.5:g=-1.5,treble=g=1.8:f=10800:width_type=q:width=0.7,"
          f"stereotools=mlev=1.0:slev={width},"
          f"acompressor=threshold=-16dB:ratio=2.2:attack=25:release=260:makeup=1,"
          f"loudnorm=I=-14:TP=-1.0:LRA=11,alimiter=limit=0.97:level=false:asc=true[out]")
    _run([C.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", mix, "-i", C.IR,
          "-filter_complex", fc, "-map", "[out]",
          "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-metadata", f"title={clean}", m4a], "master")
    mp4 = None
    if stems.get("video") and os.path.exists(stems["video"]):
        _prog(progress, 0.95, "Muxing the video…")
        mp4 = os.path.join(C.MASTERS, f"{clean}.mp4")
        try:                                               # a mux hiccup must NOT fail the whole convert —
            _run([C.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", stems["video"], "-i", m4a,
                  "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "320k",
                  "-shortest", mp4], "mux")
        except Exception:                                  # the .m4a master is already written and valid
            try:                                           # retry re-encoding odd source codecs (vp9/webm)
                _run([C.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", stems["video"], "-i", m4a,
                      "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-b:a", "320k", "-shortest", mp4], "mux-reencode")
            except Exception:
                mp4 = None                                 # audio-only master still succeeds
    try:                                                  # cover art: the original thumbnail, else a video frame
        cov = os.path.join(C.MASTERS, f"{clean}.jpg"); thumbs = glob.glob(os.path.join(work, "cover*.jpg"))
        if thumbs:
            shutil.copyfile(thumbs[0], cov)
        elif stems.get("video") and os.path.exists(stems["video"]):
            _run([C.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", "3", "-i", stems["video"],
                  "-frames:v", "1", "-vf", "scale=640:-1", cov], "cover")
    except Exception:
        pass
    os.remove(mix)
    _prog(progress, 1.0, "Done.")
    return dict(m4a=m4a, mp4=mp4)

def match_score(groove, drums):
    p = subprocess.run([C.PY_SOTA, os.path.join(C.STUDIO, "match.py"), groove, drums],
                       capture_output=True, text=True)
    try: return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception: return {"closeness": None}

# candidates the deep pass tries — span placement (thresholds) AND balance/tone (gains, hats)
_GRID = [
    dict(),                                                             # current default
    dict(clap_thr=0.90, clap_gain=0.85),                                # cover more backbeats, louder
    dict(clap_thr=1.50),                                                # sparser backbeats
    dict(include_hats=0),                                               # no hat layer
    dict(include_hats=1, hat_gain=0.50),                                # brighter hats
    dict(kick_thr=0.80, dum_gain=1.10),                                 # more / deeper kicks
    dict(kick_thr=1.15, dum_gain=0.92),                                 # sparser kicks
    dict(clap_thr=0.95, clap_gain=0.85, include_hats=1, hat_gain=0.50), # bright + full
]

def deep_match(stems, progress=None):
    """'Look at the original harder': search the groove detection (placement layer) to maximize
    measured closeness, rebuild the best groove, then re-tune the balance (dynamics layer)."""
    drums = stems["drums"]; best = None
    for i, cp in enumerate(_GRID):
        _prog(progress, 0.06 + 0.55 * i / len(_GRID), f"Studying the original… {i+1}/{len(_GRID)}")
        g = build_groove(stems, groove_params=cp)
        m = match_score(g, drums)
        if best is None or (m.get("closeness") or -1) > (best["m"].get("closeness") or -1):
            best = {"cp": cp, "m": m}
    _prog(progress, 0.64, "Rebuilding the closest groove…")
    groove = build_groove(stems, groove_params=best["cp"])
    _prog(progress, 0.74, "Re-tuning the balance…")
    params = autotune(stems, groove, use_pad=False, iters=30, seeds=(11, 23, 37), progress=None)
    return dict(groove=groove, params=params, closeness=best["m"].get("closeness"),
                detail=best["m"], groove_params=best["cp"])

def fetch_title(url):
    try:
        t = _run([C.YTDLP, "--no-update", "--no-playlist", "--skip-download",
                  "--print", "%(title)s", url], "title")
        return t.strip().splitlines()[0]
    except Exception:
        return None

def _auto_heaviness(source, vocals):
    """Auto-match the duff's heaviness to the ORIGINAL song's own low-end depth (source 40-140Hz vs
    vocal), so bass-heavy songs come out heavy on their own and vocal-forward songs stay clear — no
    manual re-match. Calibrated on real songs: POST YOU (depth ~1.7) -> ~1.0, OBVIOUS (depth ~4.6) -> 6.0."""
    try:
        import numpy as np, soundfile as sf
        from scipy.signal import butter, sosfiltfilt, resample_poly
        def _ld(p):
            x, sr = sf.read(p, always_2d=True); m = x.mean(1)
            return resample_poly(m, 48000, sr).astype("float32") if sr != 48000 else m.astype("float32")
        def _bp(x, lo, hi): return sosfiltfilt(butter(4, [lo, hi], btype="band", fs=48000, output="sos"), x)
        lo = float(np.sqrt((_bp(_ld(source), 40, 140) ** 2).mean()))
        v  = float(np.sqrt((_bp(_ld(vocals), 300, 3400) ** 2).mean())) + 1e-9
        return round(float(min(6.0, max(0.7, 1.75 * (lo / v) - 2.0))), 2)
    except Exception:
        return 0.85

# ---------------- one-shot ----------------
def convert(source, name, want_pad=False, tune=True, progress=None, sep_mode=None):
    stems  = prepare(source, name, want_pad=want_pad, progress=progress, sep_mode=sep_mode)
    groove = build_groove(stems, progress=progress)
    params = autotune(stems, groove, use_pad=want_pad, progress=progress) if tune \
             else _dyn_paths(stems, groove, want_pad)
    params["sub_vs_lead"] = _auto_heaviness(stems["source"], stems["vocals"])   # auto-match heaviness to the song
    out    = render(stems, groove, params, name, progress=progress)
    m = match_score(groove, stems["drums"])
    out.update(params=params, stems=stems, groove=groove, closeness=m.get("closeness"), match=m)
    return out

if __name__ == "__main__":
    # CLI: python engine.py "<url-or-file>" "<name>"
    r = convert(sys.argv[1], sys.argv[2], progress=lambda f, m: print(f"[{int(f*100):3d}%] {m}", flush=True))
    print(json.dumps(r, indent=2))
