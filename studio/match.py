"""Closeness meter: how faithfully the duff groove reproduces the ORIGINAL's beat.
Measures placement fidelity (do we hit where the drums hit?) + tonal balance — the things a
frame drum CAN match. It does not (and can't) score the missing melodic bed; that's by design.

Usage:  python match.py <groove.wav> <drums.wav>   ->  {"closeness": .., ...}
"""
import sys, os, json
import numpy as np
ACA = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ACA)   # Mac: self-locating root
import groove_adapt as G
SR = G.SR

def _onsets(x, lo, hi, mi, k):
    t, _ = G.onsets(x, lo, hi, mi, k); return t

def _match(a, b, tol=0.045):
    """recall = original onsets we cover; precision = our onsets that are real."""
    if len(a) == 0 or len(b) == 0: return 0.0, 0.0
    rec = float(np.mean([np.min(np.abs(a - t)) < tol for t in b]))   # of b (orig), matched by a (ours)
    pre = float(np.mean([np.min(np.abs(b - t)) < tol for t in a]))   # of a (ours), matched by b
    return rec, pre

def _bandbal(x):
    lo = G.envf(G.bp(x, 40, 140), 8); mid = G.envf(G.bp(x, 1000, 5000), 8); hi = G.envf(G.bp(x, 6000, 13000), 6)
    e = np.array([ (lo*lo).mean(), (mid*mid).mean(), (hi*hi).mean() ]); return e / (e.sum() + 1e-12)

def _env_cr(x, cr=100):                                  # loudness envelope at control rate
    hop = SR // cr; n = len(x) // hop
    return np.sqrt((x[:n*hop].reshape(n, hop) ** 2).mean(1) + 1e-12)

def _f1(rec, pre): return 2*rec*pre/(rec+pre) if (rec+pre) > 0 else 0.0

def closeness(groove_path, drums_path):
    """Closeness of the duff cover to the original's beat, on the 3 things a frame drum CAN match:
    placement (F1 of our EXACT trigger times vs the original's real onsets — not re-detected from the
    duff audio, whose ring smears hits), dynamics/feel (loudness follows the drums), and tone."""
    d = G.load48(drums_path)
    dk = _onsets(d, 40, 140, 0.09, 0.6); dc = _onsets(d, 1200, 5000, 0.08, 0.8)   # sensitive reference beat
    side = groove_path + ".onsets.json"
    if os.path.exists(side):
        o = json.load(open(side)); gk = np.array(o.get("kick", [])); gc = np.array(o.get("clap", []))
    else:                                                # fallback: detect off audio (less accurate)
        g0 = G.load48(groove_path); gk = _onsets(g0, 40, 140, 0.09, 0.9); gc = _onsets(g0, 1200, 5000, 0.08, 1.1)
    kr, kp = _match(gk, dk); cr, cp = _match(gc, dc)
    fk = _f1(kr, kp); fc = _f1(cr, cp)
    g = G.load48(groove_path); eg = _env_cr(g); ed = _env_cr(d); n = min(len(eg), len(ed))
    dyn = float(np.corrcoef(eg[:n], ed[:n])[0, 1]) if (n > 10 and eg[:n].std() > 0 and ed[:n].std() > 0) else 0.0
    if not np.isfinite(dyn): dyn = 0.0
    bd = float(np.linalg.norm(_bandbal(g) - _bandbal(d)))
    placement = 0.7*fk + 0.3*fc
    feel = max(dyn, 0.0)                                  # duff loudness follows the drums (accents/drops)
    tone = max(0.0, 1.0 - bd / 0.6)
    score = float(np.nan_to_num(100.0 * (0.45*placement + 0.35*feel + 0.20*tone)))
    return dict(closeness=round(score, 1),
                placement=round(100*placement), feel=round(100*feel), tone=round(100*tone),
                kick_f1=round(fk, 3), clap_f1=round(fc, 3), kick_recall=round(kr, 3),
                kick_prec=round(kp, 3), dyn_corr=round(dyn, 3), band_dist=round(bd, 3))

if __name__ == "__main__":
    print(json.dumps(closeness(sys.argv[1], sys.argv[2])))
