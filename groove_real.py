"""REAL-SAMPLE duff groove: trigger actual recorded frame-drum hits (VCSL, CC0, plain frame
drum = halal duff) at the song's detected beats. Faithful placement + velocity-from-original +
round-robin variants + per-hit micro-pitch + per-song tuning/brightness. Natural, not synthesized.
Reuses groove_adapt for onset detection + drum analysis.

Usage: python groove_real.py '<json or params-file>' <out_id>   (sota venv python)"""
import sys, os, json, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, soundfile as sf
from scipy.signal import resample_poly
import groove_adapt as G
SR = G.SR; D = os.path.dirname(os.path.abspath(__file__)); SDIR = os.path.join(D, "duff_samples", "framedrum")   # Mac: self-locating root

def loadsample(name):
    x, sr = sf.read(os.path.join(SDIR, name), always_2d=True, dtype="float32"); m = x.mean(1)
    if sr != SR: m = resample_poly(m, SR, sr).astype(np.float32)
    pk = np.max(np.abs(m))
    if pk > 0:
        a = int(np.argmax(np.abs(m) > 0.02*pk)); m = m[max(0, a-32):]   # trim leading silence
    return (m/(np.max(np.abs(m))+1e-9)).astype(np.float32)

def names(pat): return [os.path.basename(p) for p in sorted(glob.glob(os.path.join(SDIR, pat)))]
DUM_N = names("HDrumL_Hit_*")                                  # large open hit -> low "dum"
TEK_N = names("HDrumL_HitMuted_*") + names("HDrumS_Hit_*")     # muted / small open -> backbeat "tek"
KA_N  = names("HDrumS_HitMuted_*")                             # small muted -> soft "ka" tap

def tune(sh, ratio):                                           # sampler-style pitch shift (linear resample)
    if abs(ratio-1) < 0.004 or len(sh) < 4: return sh
    idx = np.arange(0, len(sh)-1, ratio); lo = np.clip(idx.astype(int), 0, len(sh)-2); fr = idx-lo
    return (sh[lo]*(1-fr)+sh[lo+1]*fr).astype(np.float32)
def nrm(x): return (x/(np.max(np.abs(x))+1e-9)).astype(np.float32)
def cap(sh, sec):                                              # cap ring length so busy beats don't wash
    n = int(SR*sec)
    if len(sh) > n:
        sh = sh[:n].copy(); f = int(SR*0.02); sh[-f:] *= np.linspace(1, 0, f)
    return sh

def main():
    P = dict(G.DEF); arg = sys.argv[1]
    if arg.strip().startswith("{"): P.update(json.loads(arg))
    elif os.path.exists(arg): P.update(json.load(open(arg)))
    out = P["out"]; os.makedirs(os.path.dirname(out), exist_ok=True)
    rng = np.random.default_rng(7)
    src = G.load48(P["src"]); drums = G.load48(P["drums"]); N = len(src)
    kt, ks = G.onsets(drums, 40, 140, 0.09, P["kick_thr"])
    ct, cs = G.onsets(drums, 1200, 5000, 0.08, P["clap_thr"])
    ht, hs = G.onsets(drums, 6000, 13000, 0.045, P["hat_thr"])
    def dd(t, s, ref, gap):
        if not len(t) or not len(ref): return t, s
        keep = [i for i, x in enumerate(t) if np.min(np.abs(ref-x)) > gap]; return t[keep], s[keep]
    ct, cs = dd(ct, cs, kt, 0.05); ht, hs = dd(ht, hs, ct, 0.04); ht, hs = dd(ht, hs, kt, 0.04)
    A = G.analyze(drums, kt, ct, ht)
    dum_tune = float(np.clip(A["kick_f0"]/74.0, 0.70, 1.30))   # deeper kick song -> deeper dum
    tone = float(A["tone_lp"])
    # pick the frame-drum VOICE to match the song's beat: open(ring) vs muted(tight), large vs small
    deep = A["kick_f0"] < 70
    sz = "L" if deep else "S"
    dum_files = names(f"HDrum{sz}_Hit_*")                        # OPEN hits = lively body/ring (muted = dull/dead)
    tek_files = names("HDrumS_HitMuted_*") + names("HDrumL_HitMuted_*")   # muted -> tight backbeat
    ka_files  = names("HDrumS_HitMuted_*")
    dum_cap = float(np.clip(A["kick_tau"]*3.0, 0.22, 0.62))      # tight kick -> short punchy open hit; boomy -> longer ring
    DUMS = [nrm(cap(G.lp(tune(loadsample(n), dum_tune), min(tone, 9500)), dum_cap)) for n in dum_files]
    TEKS = [nrm(cap(G.lp(loadsample(n), tone), 0.30)) for n in tek_files]
    KAS  = [nrm(cap(G.lp(loadsample(n), tone), 0.18)) for n in ka_files]
    SPARK = [nrm(cap(tune(loadsample(n), 1.15), 0.16)) for n in names("HDrumS_Hit_*")]  # bright small-open sparkle layer (keeps highs)
    kit = f"{sz}_Hit_cap{round(dum_cap,2)}+spark"
    L = np.zeros(N+SR, np.float32); R = np.zeros(N+SR, np.float32)
    def place(shot, t, g, pan):
        i = int(t*SR); j = min(len(L), i+len(shot)); k = j-i
        if k <= 0: return
        gl = g*np.cos((pan*0.5+0.5)*np.pi/2); gr = g*np.sin((pan*0.5+0.5)*np.pi/2); L[i:j]+=shot[:k]*gl; R[i:j]+=shot[:k]*gr
    kE = G.envf(G.bp(drums,40,140),6); cE = G.envf(G.bp(drums,1200,5000),6); hE = G.envf(G.bp(drums,6000,13000),5)
    def vels(E, times):
        if not len(times): return np.array([])
        v = np.array([float(E[int(t*SR):int((t+0.015)*SR)].max()) if int(t*SR) < len(E) else 0.0 for t in times])
        return np.clip(v/(np.percentile(v,92)+1e-9), 0, 1.25)
    kv, cv, hv = vels(kE,kt), vels(cE,ct), vels(hE,ht)
    def gv(v): return 0.20+0.85*float(v)
    def hit(pool, t, g, pan):
        if not pool: return
        sh = tune(pool[int(rng.integers(len(pool)))], float(rng.uniform(0.97,1.03)))   # round-robin + micro-pitch
        place(sh, t+float(rng.uniform(-0.004,0.004)), g*10**(rng.uniform(-1.4,1.4)/20), pan+float(rng.uniform(-0.05,0.05)))
    for t, v in zip(kt, kv):
        hit(DUMS, t, P["dum_gain"]*gv(v), 0.0)            # body
        hit(SPARK, t, P["dum_gain"]*0.32*gv(v), 0.0)      # bright sparkle layer stacked on top
    for n_, (t, v) in enumerate(zip(ct, cv)): hit(TEKS, t, P["clap_gain"]*gv(v), (0.12 if n_%2 else -0.12))
    if P["include_hats"]:
        for n_, (t, v) in enumerate(zip(ht, hv)): hit(KAS, t, P["hat_gain"]*0.8*gv(v), (0.3 if n_%2 else -0.3))
    st = np.stack([L[:N], R[:N]], 1); st /= (np.max(np.abs(st))+1e-9); st *= 0.92
    sf.write(out, st, SR, subtype="PCM_24")
    json.dump({"kick": [round(float(x),4) for x in kt], "clap": [round(float(x),4) for x in ct],
               "hat": [round(float(x),4) for x in ht]}, open(out+".onsets.json", "w"))   # exact trigger times
    print(json.dumps({"kit": kit, "dum_tune": round(dum_tune,3), "kick_f0": A["kick_f0"], "kick_tau": A["kick_tau"],
                      "kick": len(kt), "clap": len(ct), "hat": len(ht), "out": out}))
if __name__ == "__main__": main()
