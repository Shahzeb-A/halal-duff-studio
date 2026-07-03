"""Generalized dynamic engine (any song). Levels a heavy duff-sub + optional sung pad to the
ORIGINAL's own dynamics, read off its stems. Path-parameterized version of obvious_dyn.py.

All I/O paths come from the params JSON:
  lead   (required)  isolated lead vocal
  drums  (required)  drums stem (beat onsets + low-band 'where it's heavy')
  duff   (required)  the real-frame-drum groove (groove_real output)
  other  (optional)  instrument stem — target dynamics for a sung pad
  pad_raw(optional)  the singer-voice pad (RVC) to level-match; omit/pad_off=1 -> voice+duff only
  ir     (optional)  reverb IR for the pad send

Modes:  analyze <p.json> | score <p.json> | search <p.json> <seed> <n> | render <p.json> <out.wav>
"""
import sys, os, json
import numpy as np, soundfile as sf
ACA = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ACA)   # Mac: self-locating root
import groove_adapt as G
from scipy.signal import butter, sosfiltfilt, fftconvolve

SR = G.SR; CR = 100
DEF = dict(pad_floor=0.12, pad_follow=1.0, pad_hp=170, pad_level=0.85, pad_rev=0.18,
           sub_gain=1.15, sub_lp=95, sub_follow=1.5, duff_level=1.0, duck=0.55, pad_off=0, sub_vs_lead=0.85)
TUNE = ("pad_floor","pad_follow","pad_hp","pad_level","pad_rev","sub_gain","sub_lp","sub_follow","duff_level","duck")

def lp(x, hi, o=4): return sosfiltfilt(butter(o, hi, btype="low", fs=SR, output="sos"), x).astype(np.float32)
def hp(x, lo, o=2): return sosfiltfilt(butter(o, lo, btype="high", fs=SR, output="sos"), x).astype(np.float32)
def bp(x, lo, hi, o=4): return sosfiltfilt(butter(o, [lo,hi], btype="band", fs=SR, output="sos"), x).astype(np.float32)
def env_cr(x, cr=CR):
    hop=SR//cr; n=len(x)//hop
    if n < 2: return np.zeros(2, np.float32)
    f=x[:n*hop].reshape(n, hop); return np.sqrt((f*f).mean(1)+1e-12).astype(np.float32)
def up(e, N, cr=CR):
    r=np.repeat(e, SR//cr)
    if len(r)<N: r=np.pad(r,(0,N-len(r)),mode="edge")
    return r[:N].astype(np.float32)
def pct(x, p): return float(np.percentile(x, p)) if len(x) else 0.0
def corr(a, b):
    n=min(len(a),len(b)); a=a[:n]-a[:n].mean(); b=b[:n]-b[:n].mean()
    d=np.sqrt((a*a).sum()*(b*b).sum()); return float((a*b).sum()/d) if d>0 else 0.0
def smooth_cr(e, win):
    if win<2: return e
    k=np.ones(win)/win; return np.convolve(e, k, mode="same").astype(np.float32)

_C=None
def load_all(P):
    global _C
    if _C is not None: return _C
    use_pad = bool(P.get("pad_raw")) and bool(P.get("other")) and not P.get("pad_off")
    lead=G.load48(P["lead"]); N=len(lead)
    def fit(x): return np.pad(x,(0,N-len(x)))[:N] if len(x)<N else x[:N]
    drums=fit(G.load48(P["drums"])); duff=fit(G.load48(P["duff"]))
    other = fit(G.load48(P["other"]))   if use_pad else None
    padr  = fit(G.load48(P["pad_raw"])) if use_pad else None
    e_lead=env_cr(lead); e_low=env_cr(lp(drums,120)); e_other=env_cr(other) if other is not None else None
    T=dict(other_ratio=(pct(e_other,60)/(pct(e_lead,60)+1e-9)) if e_other is not None else 0.0,
           low_ratio=pct(e_low,70)/(pct(e_lead,60)+1e-9), lead_pk=pct(e_lead,90), n=N)
    _C=dict(lead=lead,padr=padr,other=other,drums=drums,duff=duff,N=N,use_pad=use_pad,
            e_lead=e_lead,e_other=e_other,e_low=e_low,T=T,ir=P.get("ir"))
    return _C

def build(P, stereo, C=None):
    C=C or load_all(P); lead=C["lead"]; drums=C["drums"]; duff=C["duff"]
    N=C["N"]; e_lead=C["e_lead"]; T=C["T"]
    # --- PAD (optional): follow the original instrument envelope, level to the original instrument->vocal ratio ---
    if C["use_pad"]:
        e_other=C["e_other"]; e_on=np.clip(e_other/(pct(e_other,95)+1e-9),0,1.6)
        pad=hp(C["padr"], P["pad_hp"]) * up(P["pad_floor"]+P["pad_follow"]*e_on, N)
        g_pad=(T["other_ratio"]*P["pad_level"])*pct(e_lead,60)/(pct(env_cr(pad),60)+1e-9)
        pad=pad*g_pad
    else:
        pad=np.zeros(N, np.float32)
    # --- HEAVY DUFF: deep sub layer, swell follows the original's heavy (kick) sections; magnitude is a knob ---
    slow=smooth_cr(env_cr(lp(drums,120)), int(CR*0.5)); slow=np.clip(slow/(pct(slow,90)+1e-9),0,1.4)
    sub=lp(duff, P["sub_lp"]) * up(1.0+P["sub_follow"]*slow, N) * P["sub_gain"]
    duff_h=(duff + sub) * P["duff_level"]
    duck=1.0-P["duck"]*np.clip(up(smooth_cr(e_lead,3),N)/(T["lead_pk"]+1e-9),0,1.0)
    duff_h=duff_h*duck
    if not stereo:
        return lead+pad+duff_h, pad, duff_h
    # CLARITY GUARD (render): cap the duff's LOW band (40-140Hz — the added sub AND the frame drum's own
    # deep fundamental, which alone is ~70% sub) against the lead's VOCAL band (300-3400Hz), so the voice
    # sits on top even on very bass-heavy songs. Master vocal/sub energy ~ (1/target)^2; 0.70 -> ~2.0.
    # Only ever caps DOWN, so naturally-clear songs (ADAKAARI, Bewajah...) are untouched.
    _lv=float(np.sqrt((bp(lead,300,3400)**2).mean()))
    _dl=float(np.sqrt((bp(duff_h,40,140)**2).mean()))+1e-9
    _tgt=P.get("sub_vs_lead",0.85)*_lv                    # user-controllable: high = heavy beat, low = clear vocal
    if _tgt>1e-5 and _dl>_tgt:
        _low=lp(duff_h,160); duff_h=(_low*(_tgt/_dl))+(duff_h-_low)   # scale only the low band down
    # reverb send on the pad + light haas width
    if C["use_pad"] and C.get("ir") and os.path.exists(C["ir"]):
        ir=G.load48(C["ir"]); wet=fftconvolve(pad, ir)[:N]
        wet=wet/(np.max(np.abs(wet))+1e-9)*(np.max(np.abs(pad))+1e-12); pad=pad+P["pad_rev"]*wet
    R_pad=np.pad(pad,(9,0))[:N]
    # STEREO WIDTH ("surround / physical"): vocal + deep sub stay dead-center (solid punch + mono-safe
    # bass), while the duff's mid/high body is Haas-delayed on R so the beat spreads wide and immersive.
    _haas=max(1,int(SR*P.get("width_ms",12.0)/1000.0))
    _dlow=lp(duff_h,180); _dhi=duff_h-_dlow
    _dhiR=np.pad(_dhi,(_haas,0))[:N]
    L=lead+pad+duff_h; R=lead+R_pad+_dlow+_dhiR
    st=np.stack([L,R],1); st=st/(np.max(np.abs(st))+1e-9)*0.985
    return st.astype(np.float32), T

def score(P, C=None):
    C=C or load_all(P); mix,pad,duff_h = build(P, False, C)
    low_corr=corr(env_cr(lp(mix,120)), C["e_low"]); e_lead=C["e_lead"]; T=C["T"]
    low_ratio=pct(env_cr(lp(mix,90)),70)/(pct(e_lead,60)+1e-9)
    if C["use_pad"]:
        pad_corr=corr(env_cr(pad), C["e_other"])
        pad_ratio=pct(env_cr(pad),60)/(pct(e_lead,60)+1e-9)
        pad_err=abs(pad_ratio-T["other_ratio"])/(T["other_ratio"]+1e-9)
        mud=pct(env_cr(bp(pad,250,650)),70)/(pct(e_lead,60)+1e-9)
    else:
        pad_corr, pad_err, mud = 0.0, 0.0, 0.0
    peak=float(np.max(np.abs(mix)))
    obj=1.3*pad_corr + 1.7*low_corr - 0.7*pad_err - 0.5*min(mud,1.0)
    return dict(obj=round(obj,4), pad_corr=round(pad_corr,3), low_corr=round(low_corr,3),
                pad_err=round(pad_err,3), low_ratio=round(low_ratio,4), mud=round(mud,3), peak=round(peak,3))

BOUNDS=dict(pad_floor=(0.05,0.30), pad_follow=(0.6,1.6), pad_hp=(120,240), pad_level=(0.7,1.05),
            sub_gain=(0.9,1.5), sub_lp=(70,130), sub_follow=(0.8,2.2), duck=(0.35,0.70))
def search(P, seed, n):
    C=load_all(P); rng=np.random.default_rng(seed); best=None
    for _ in range(n):
        Q=dict(P)
        for k,(lo,hi) in BOUNDS.items(): Q[k]=float(rng.uniform(lo,hi))
        m=score(Q,C)
        if best is None or m["obj"]>best[1]["obj"]: best=(Q,m)
    out={k:round(best[0][k],4) for k in BOUNDS}
    print(json.dumps({"params":out, "metrics":best[1]}))

def main():
    mode=sys.argv[1]; P=dict(DEF); P.update(json.load(open(sys.argv[2])))
    if mode=="analyze": print(json.dumps({k:round(v,4) for k,v in load_all(P)["T"].items()}))
    elif mode=="score": print(json.dumps(score(P)))
    elif mode=="search": search(P, int(sys.argv[3]), int(sys.argv[4]))
    elif mode=="render":
        st,T=build(P, True); sf.write(sys.argv[3], st, SR, subtype="PCM_24")
        print(json.dumps({"out":sys.argv[3], "metrics":score(P)}))
if __name__=="__main__": main()
