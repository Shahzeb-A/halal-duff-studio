"""Pod-side GPU separation for Halal Duff Studio (runs ON a RunPod GPU pod, not the Mac).

Input  : a source .wav
Output : <out_dir>/drums.wav  (demucs htdemucs_ft)  +  <out_dir>/vocals.wav  (BS-Roformer)
Prints : DRUMS=<path> and VOCALS=<path> as its last two stdout lines (the Mac reads these).

On a real GPU (lots of VRAM) both steps are fast and there is no 8GB memory problem, so this
uses CUDA and the full-quality models. Halal boundary is unaffected — this only isolates the
song's OWN drums + vocal stems; the voice+duff cover is still built locally on the Mac.

Usage:  python pod_separate.py <source.wav> <out_dir>
"""
import sys, os, glob, time, shutil
import torch, soundfile as sf, numpy as np
from demucs.pretrained import get_model
from demucs.apply import apply_model
from demucs.audio import convert_audio

src, outdir = sys.argv[1], sys.argv[2]
os.makedirs(outdir, exist_ok=True)
if not torch.cuda.is_available():
    # This runs on a RENTED GPU pod. Refuse to silently fall back to CPU — that's 10-20x slower work
    # billed at GPU price. Exit non-zero so the Mac's _run_sep (_ssh check=True) fails the job loudly
    # and separate()'s finally terminates the pod immediately.
    print("FATAL: CUDA not available on this pod — refusing to run CPU-speed work at GPU price", flush=True)
    sys.exit(3)
dev = "cuda"
t0 = time.time()
def log(m): print(f"[{time.time()-t0:6.0f}s] {m}", flush=True)
log(f"GPU: {torch.cuda.get_device_name(0)}")

# ---------------- beat: demucs htdemucs_ft on GPU ----------------
log(f"demucs htdemucs_ft on {dev} ...")
m = get_model("htdemucs_ft"); m.to(dev).eval()
wav, sr = sf.read(src, dtype="float32", always_2d=True)
wav = convert_audio(torch.from_numpy(wav.T).float(), sr, m.samplerate, m.audio_channels)
ref = wav.mean(0); mean, std = ref.mean(), ref.std()
x = ((wav - mean) / (std + 1e-8)).unsqueeze(0).to(dev)
with torch.no_grad():
    s = apply_model(m, x, device=dev, split=True, overlap=0.25)[0]
s = (s * std + mean).cpu().numpy()
drums = os.path.join(outdir, "drums.wav")
sf.write(drums, s[m.sources.index("drums")].T, m.samplerate, subtype="PCM_24")
del m, s, x
if dev == "cuda": torch.cuda.empty_cache()
log(f"drums -> {drums}")

# ---------------- vocal: BS-Roformer via audio-separator on GPU ----------------
from audio_separator.separator import Separator
mdir = os.environ.get("POD_MODEL_DIR")   # persist roformer weight on the network volume across sessions
sep = Separator(output_dir=outdir, output_format="WAV", **({"model_file_dir": mdir} if mdir else {}))
sep.load_model(model_filename="model_bs_roformer_ep_317_sdr_12.9755.ckpt")
outs = sep.separate(src) or []
paths = [f if os.path.isabs(f) else os.path.join(outdir, f) for f in outs]
voc = [p for p in paths if "vocal" in os.path.basename(p).lower()]
if not voc:
    voc = [p for p in glob.glob(os.path.join(outdir, "*.wav")) if "vocal" in os.path.basename(p).lower()]
if not voc:
    raise RuntimeError("roformer produced no vocals stem")
vocals = os.path.join(outdir, "vocals.wav")
if os.path.abspath(voc[0]) != os.path.abspath(vocals):
    shutil.copyfile(voc[0], vocals)
log(f"vocals -> {vocals}")

print("DRUMS=" + drums, flush=True)
print("VOCALS=" + vocals, flush=True)
