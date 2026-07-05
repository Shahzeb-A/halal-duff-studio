"""Halal Duff Studio — paths & interpreters. Voice + duff only; no melodic instruments (fiqh boundary)."""
import os

ACA      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Mac: self-locating root ($ROOT/studio/config.py -> $ROOT)
STUDIO   = os.path.join(ACA, "studio")
WORK     = os.path.join(STUDIO, "work")               # per-song working dirs
MASTERS  = os.path.join(ACA, "Masters")               # final outputs "<Song>.m4a/.mp4"
SAMPLES  = os.path.join(ACA, "duff_samples", "framedrum")
IR       = os.path.join(ACA, "reverb_ir_clean.wav")
ROFORMER = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"

# ONE venv for everything (DSP, groove, dynamic engine, vocal isolation, demucs). Cross-platform:
# Windows keeps its interpreter at .venv\Scripts\python.exe, macOS/Linux at .venv/bin/python.
_VENV_PY = os.path.join(ACA, ".venv", "Scripts", "python.exe") if os.name == "nt" \
           else os.path.join(ACA, ".venv", "bin", "python")
# Fall back to whatever interpreter is running the server if the venv layout differs (e.g. conda).
import sys as _sys
PY_SOTA  = _VENV_PY if os.path.exists(_VENV_PY) else _sys.executable
AUDIOSEP_CMD = [PY_SOTA, "-m", "audio_separator.utils.cli"]   # dead path (separate_vocal.py uses the Python API); kept for ref
PY_BASE  = PY_SOTA

# tools
YTDLP    = "yt-dlp"
FFMPEG   = "ffmpeg"

# separation backend: "local" (CPU on this 8GB Mac) or "runpod" (GPU offload, see runpod_sep.py)
SEP_MODE = os.environ.get("DUFF_SEP", "local")

for d in (WORK, MASTERS):
    os.makedirs(d, exist_ok=True)
