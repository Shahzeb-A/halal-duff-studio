"""Isolate the lead vocal with BS-Roformer via the audio-separator PYTHON API (the console-script
.exe / `-m` CLI don't work in this venv). Prints  VOCALS=<path>  as its last stdout line.

Usage:  python separate_vocal.py <input.wav> <output_dir> <model_filename>
"""
import sys, os, glob
import torch
torch.backends.mps.is_available = lambda: False  # 8GB Mac: force roformer to CPU (MPS OOMs on a full song; CPU ~10min is the documented path)
from audio_separator.separator import Separator

inp, outdir, model = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(outdir, exist_ok=True)
sep = Separator(output_dir=outdir, output_format="WAV")
sep.load_model(model_filename=model)
outs = sep.separate(inp) or []
paths = [f if os.path.isabs(f) else os.path.join(outdir, f) for f in outs]
voc = [p for p in paths if "vocal" in os.path.basename(p).lower()]
if not voc:
    voc = [p for p in glob.glob(os.path.join(outdir, "*.wav")) if "vocal" in os.path.basename(p).lower()]
print("VOCALS=" + (voc[0] if voc else ""))
