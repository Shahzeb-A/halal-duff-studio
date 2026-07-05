"""Generic: extract the drums stem from one audio file. Usage: python extract_drums_one.py <in> <out>"""
import sys, soundfile as sf, numpy as np, torch
from demucs.pretrained import get_model
from demucs.apply import apply_model
from demucs.audio import convert_audio
inp, out = sys.argv[1], sys.argv[2]
stem = sys.argv[3] if len(sys.argv) > 3 else "drums"   # drums | other | bass | vocals
dev = "cuda" if torch.cuda.is_available() else "cpu"   # CUDA on Win/Linux GPUs; Mac has no CUDA -> CPU (MPS OOMs on 8GB)
m = get_model("htdemucs_ft"); m.to(dev).eval()
wav, sr = sf.read(inp, dtype="float32", always_2d=True)
wav = convert_audio(torch.from_numpy(wav.T).float(), sr, m.samplerate, m.audio_channels)
ref = wav.mean(0); mean, std = ref.mean(), ref.std()
x = ((wav-mean)/(std+1e-8)).unsqueeze(0).to(dev)
with torch.no_grad():
    s = apply_model(m, x, device=dev, split=True, overlap=0.25)[0]
s = (s*std+mean).cpu().numpy()
sf.write(out, s[m.sources.index(stem)].T, m.samplerate, subtype="PCM_24")
print(stem, "saved", out)
