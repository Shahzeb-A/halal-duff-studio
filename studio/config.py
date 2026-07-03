"""Halal Duff Studio — paths & interpreters. Voice + duff only; no melodic instruments (fiqh boundary)."""
import os

ACA      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Mac: self-locating root ($ROOT/studio/config.py -> $ROOT)
STUDIO   = os.path.join(ACA, "studio")
WORK     = os.path.join(STUDIO, "work")               # per-song working dirs
MASTERS  = os.path.join(ACA, "Masters")               # final outputs "<Song>.m4a/.mp4"
SAMPLES  = os.path.join(ACA, "duff_samples", "framedrum")
IR       = os.path.join(ACA, "reverb_ir_clean.wav")
ROFORMER = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"

# Mac: ONE venv (CPU/MPS) for everything — DSP, groove, dynamic engine, vocal isolation, demucs.
PY_SOTA  = os.path.join(ACA, ".venv", "bin", "python")
AUDIOSEP_CMD = [PY_SOTA, "-m", "audio_separator.utils.cli"]   # dead path (separate_vocal.py uses the Python API); kept for ref
# No CUDA on Mac; demucs runs under the same venv (mps/cpu). Both interpreters are the one venv.
PY_BASE  = PY_SOTA

# tools
YTDLP    = "yt-dlp"
FFMPEG   = "ffmpeg"

# separation backend: "local" (CPU on this 8GB Mac) or "runpod" (GPU offload, see runpod_sep.py)
SEP_MODE = os.environ.get("DUFF_SEP", "local")

for d in (WORK, MASTERS):
    os.makedirs(d, exist_ok=True)
