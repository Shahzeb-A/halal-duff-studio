<div align="center">

# 🎵 Halal Duff Studio

### Turn any song into a *halal* version — its own voice, carried by a real frame-drum.

No melodic instruments. No synths. Just the original vocal and an authentic **duff** groove,
automatically matched to the song so it still hits.

[![License: MIT](https://img.shields.io/badge/License-MIT-3DE9C0.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-23B5E8.svg)](https://www.python.org/)
[![Platform: macOS](https://img.shields.io/badge/Platform-macOS-0A1326.svg)](#-getting-started--pick-your-lane)
![Voice + Duff only](https://img.shields.io/badge/Sound-voice%20%2B%20duff%20only-3DE9C0.svg)
![No framework frontend](https://img.shields.io/badge/Frontend-hand--built%2C%20zero%20deps-23B5E8.svg)

<br/>

<!-- 🎬 LinkedIn / demo: drop a short screen-capture GIF or video here.
     e.g.  ![demo](docs/demo.gif)   or an MP4 uploaded to the release. -->
<em>Paste a link → get a clean voice + duff cover in your own Spotify-style library.</em>

</div>

---

## Why

A lot of people who love music also keep to a **voice-and-percussion-only** listening tradition. The usual
options are hard-to-find nasheed covers or muddy phone recordings. **Halal Duff Studio** takes a track you
already like, isolates the human voice, and rebuilds the low end with a **real frame-drum (duff)** — not a
synth pad, not a guitar imitation — so you get a clean, full, listenable version that stays within that line.

> **Design invariant:** the output is *strictly* **voice + duff**. There is no melodic-instrument path, no
> sung "instrument" mimicry, no re-pitched pad. That boundary is enforced in the engine, not left to a
> setting. *(This tool is not a fatwa — the religious call is yours; it just refuses to cross the line for you.)*

---

## What it does

| | |
|---|---|
| 🎤 **Isolates the real vocal** | State-of-the-art separation (BS-Roformer) pulls a clean lead vocal. |
| 🥁 **Real duff, not a synth** | Groove built from **CC0 frame-drum samples**, arranged to the song's beat. |
| 🎚️ **Auto-heaviness match** | The "water-beat" depth is measured from the *original* and matched automatically. |
| 🎧 **Sounds full, stays mono-safe** | Haas-width stereo on the drum highs, sub kept centered; a clarity guard keeps the voice on top. |
| 📚 **Spotify-style library** | Everything you make lands in a browsable library with covers, playlists, search, and a video tab. |
| ⚡ **Local or GPU** | Runs entirely on your Mac, or offloads the heavy separation to a **RunPod** GPU — spun up and torn down per song. |

---

## 🚀 Getting started — pick your lane

Three ways in, depending on how technical you want to be.

<details open>
<summary><h3>🌱 &nbsp;I just want to use it (no coding)</h3></summary>

You don't need to understand any of the code. The easiest path is to let **[Claude Code](https://claude.com/claude-code)**
(Anthropic's AI coding assistant) do the whole setup for you — installing, downloading, and launching.

**1.** Install Claude Code (one command in the macOS **Terminal** app — copy, paste, press Enter):

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

**2.** Start it and just *ask it in plain English*:

```bash
claude
```

Then type (or paste) something like:

> **"Clone the repo https://github.com/Shahzeb-A/halal-duff-studio, set it up completely for me, and launch the app."**

Claude will install the bits it needs (ffmpeg, Python, the dependencies), build everything, and open the app
in your browser at **http://127.0.0.1:7860**. If anything's missing it'll tell you exactly what to do.

**3.** From then on, just **double-click `Halal Duff Studio.app`** to open it. Paste a YouTube link, press
**Convert**, and your halal version shows up in the Library. That's it. 🎧

*(Prefer clicking to typing? You can also [download the repo as a ZIP](https://github.com/Shahzeb-A/halal-duff-studio/archive/refs/heads/main.zip),
unzip it, and hand the folder to Claude — "set up and run the app in this folder.")*

</details>

<details>
<summary><h3>🔧 &nbsp;I'm a developer</h3></summary>

**Requirements:** macOS 12+, [Homebrew](https://brew.sh), Python 3.12, `ffmpeg`, `yt-dlp`.

```bash
# 1. clone
git clone https://github.com/Shahzeb-A/halal-duff-studio.git
cd halal-duff-studio

# 2. system tools
brew install ffmpeg yt-dlp

# 3. python env
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 4. run
cd studio && ../.venv/bin/python server.py
#   → http://127.0.0.1:7860
```

Or double-click **`Halal Duff Studio.app`** (a tiny bundle that health-checks the local server and opens it).

The backend is a single FastAPI file (`studio/server.py`) over a plain-Python pipeline; the frontend is one
hand-written `studio/web/index.html` with **no build step and no JS framework**. Hack away.

</details>

<details>
<summary><h3>🧪 &nbsp;I'm an enthusiast — show me how it works</h3></summary>

Under the hood it's a small, readable pipeline — no magic, no black-box model you can't inspect:

```
YouTube link
   │ yt-dlp
   ▼
source audio ──► SEPARATION ──►  clean lead vocal          (demucs + BS-Roformer)
                    │            + isolated drums
                    ▼
             DUFF GROOVE  ──►  real frame-drum hits         (CC0 VCSL samples,
             on the beat        arranged to the onsets        velocity from the original)
                    │
                    ▼
           DYNAMIC MASTER ──►  voice + duff, matched         (auto-heaviness, clarity guard,
                                                              Haas stereo width, convolution space)
                    │ ffmpeg
                    ▼
        Masters/<song>.m4a  (+ .mp4 video, + .jpg cover)
```

Things you can play with:
- **The tuning sliders** in the app (heaviness, duff level, sub depth, beat swell, brightness, vocal duck) →
  hit **Re-render** for a seconds-fast remix that reuses the stems.
- **"Match closer to the original"** runs a headless search over the groove/balance to tighten the beat match,
  and reports an honest placement / feel / tone breakdown (it won't fake a gain it can't get).
- **The DSP** lives in `studio/dyn.py` (the master) and `groove_real.py` (the drum arranger) — all NumPy/SciPy,
  fully readable.
- **GPU offload** (`studio/runpod_sep.py`) is a self-contained on-demand RunPod controller if you want the fast lane.

Everything melodic in the original is discarded — only the **vocal stem** and the **duff** reach the master.

</details>

### First-launch notes

- **The very first conversion also downloads the AI separation models** (~1 GB, one-time) on top of the
  usual processing — so your first song takes longer than later ones. After that they're cached.
- **Downloaded the repo as a ZIP instead of cloning?** macOS may quarantine the app — the first time,
  **right-click `Halal Duff Studio.app` → Open** (then it's trusted); if double-clicking does nothing, run
  once: `chmod +x "Halal Duff Studio.app/Contents/MacOS/halal-duff-studio"`. *(Cloning with `git` — including
  the Claude Code path above — sets this up automatically, so the recommended path just works.)*

---

## ⚡ Optional: the fast lane (RunPod GPU)

Separation is the heavy step. On an 8 GB Mac it runs on CPU (~10–15 min a song, but free). To make it ~2 min:

1. Get a [RunPod](https://runpod.io) API key (Settings → API Keys).
2. In the app, open **⚙ Settings → RunPod key**, paste it **once**. You can **edit or replace it any time** from
   the same place — it's stored locally at `~/.runpod_key` (permissions `600`) and never leaves your machine.
3. Tick the **⚡ RunPod GPU** box before converting.

**How the billing works — and why it's safe:**

- A GPU pod is spun up **fresh for each conversion**, does only the separation, and is **always torn down the
  moment it's done** — so you pay for the actual minutes and nothing after.
- The **next** conversion starts a **brand-new** pod. It never tries to reuse a closed one.
- Cleanup is guaranteed on every exit path — normal finish, error, or crash — with retries, and a **pod-side
  dead-man switch** that self-destructs the pod if your machine ever disappears (wifi drop, power loss).
- The tool **only ever terminates the pod it created** (by pod ID). It will never touch anything else in your
  RunPod account.
- If no GPU has capacity it fails cleanly with a clear message (and leaves nothing running) — you can retry or
  just uncheck the box to run locally on CPU.

Default is **off** (free local CPU). GPU is purely opt-in.

---

## 🧱 Tech stack

- **Backend** — FastAPI + Uvicorn; a small threaded job model with progress polling; a same-origin CSRF guard
  and JSON error contract so the loopback server is safe to run.
- **Frontend** — one hand-built single-page app (vanilla JS, **no framework, no build**): custom transport,
  waveform canvas, playlists, cover art, filter, settings. DevelMo brand: navy `#0A1326`, mint→cyan gradient,
  Poppins + Inter.
- **Audio** — `demucs` (htdemucs_ft), `audio-separator` (BS-Roformer), NumPy/SciPy DSP, `ffmpeg` mastering.
- **GPU** — on-demand RunPod pods via the `runpod` SDK, with capacity fallback across GPU types and clouds.

```
studio/
  server.py        FastAPI app — jobs, library, media, playlists, config
  engine.py        pipeline orchestration (download → separate → groove → master)
  dyn.py           dynamic master: auto-heaviness, clarity guard, stereo width
  match.py         beat/heaviness matching against the original
  runpod_sep.py    on-demand GPU separation (always-terminate guaranteed)
  pod_separate.py  the code that runs on the pod
  web/index.html   the whole frontend
duff_samples/      CC0 frame-drum one-shots (VCSL)
```

---

## 🕌 The halal boundary

The duff (frame drum) is permitted *because* it is non-melodic percussion — it may only carry **rhythm**; the
melody must stay in the **voice**. So this tool:

- **never** pitch-shifts the duff to play the song's actual notes,
- **never** synthesizes or imitates a melodic instrument, and
- **never** reintroduces the original's instrumental bed.

The retired experimental "pad" path is structurally dead — the engine raises rather than produce anything
melodic. Output is voice + frame-drum, full stop.

---

## Credits & licensing

- **Frame-drum samples** — [Versilian Community Sample Library (VCSL)](https://github.com/sgossner/VCSL), **CC0**.
- **Separation models** — Demucs (Meta) and BS-Roformer via [`audio-separator`](https://github.com/nomad-audio/python-audio-separator).
- **Code** — MIT, © 2026 Shahzeb Ali. Built under [**DevelMo Technologies**](https://develmo.com).

> ⚠️ **You are responsible for the rights to any audio you process.** This tool ships **no** copyrighted music —
> your rendered `Masters/` folder stays on your machine and is git-ignored. Use it on content you have the right to use.

---

<div align="center">

*Built with care to keep the music halal — voice and duff, nothing else.*

</div>
