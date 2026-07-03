"""ADAPTIVE replication groove: analyze the ORIGINAL drums' character (kick pitch+decay,
clap brightness+decay, hat brightness, overall tone) and SHAPE the duff to match THIS song,
instead of one generic duff for all. Placement stays faithful (hits where the song hits).

Usage: python groove_adapt.py '<json params>' <out_id>   (sota venv python)"""
import sys, json, os
import numpy as np, soundfile as sf
from scipy.signal import butter, sosfiltfilt, resample_poly, find_peaks, fftconvolve
SR = 48000; D = os.path.dirname(os.path.abspath(__file__))   # Mac: self-locating project root
DEF = dict(kick_thr=0.95, clap_thr=1.25, hat_thr=1.15, include_hats=1, quantize=0.0,
           dum_gain=1.0, clap_gain=0.66, hat_gain=0.34, accent=0.55,
           src="", drums="", out="")

def load48(p):
    x, sr = sf.read(p, dtype="float32", always_2d=True); m = x.mean(1)
    return resample_poly(m, SR, sr).astype(np.float32) if sr != SR else m
def bp(x, lo, hi, o=4): return sosfiltfilt(butter(o,[lo,hi],btype="band",fs=SR,output="sos"), x)
def lp(x, hi, o=4): return sosfiltfilt(butter(o,hi,btype="low",fs=SR,output="sos"), x)
def envf(x, ms=8):
    w=max(1,int(SR*ms/1000)); win=np.hanning(w); win/=win.sum(); return fftconvolve(np.abs(x),win,mode="same")
def onsets(x, lo, hi, mi, k):
    e=envf(bp(x,lo,hi)); f=np.diff(e,prepend=e[0]); f[f<0]=0
    if f.max()<=0: return np.array([]), np.array([])
    f/=f.max(); h=max(0.06, f.mean()+k*f.std())
    pk,_=find_peaks(f,height=h,distance=int(mi*SR)); s=f[pk]
    return pk/SR, (s/(s.max()+1e-9) if len(s) else s)

# ---------- ANALYZE the original drum character ----------
def spectral_centroid(seg):
    w = seg*np.hanning(len(seg)); mag = np.abs(np.fft.rfft(w)); fr = np.fft.rfftfreq(len(seg), 1/SR)
    return float((fr*mag).sum()/(mag.sum()+1e-9))
def decay_tau(seg):
    # boominess = tail-energy ratio (tight kick -> ~0, boomy/ringing kick -> higher)
    env = envf(seg, ms=8); p = np.argmax(env); e = env[p:]
    if e.size < 8 or e[0] <= 0: return 0.14
    b = int(0.05*SR); mx = int(0.25*SR)
    body = float((e[:b]**2).sum()); tail = float((e[b:mx]**2).sum())
    r = tail/(body+tail+1e-9)
    return float(0.07 + 0.34*r)

def analyze(drums, kt, ct, ht):
    kt = kt[np.argsort(-np.array([1]*len(kt)))][:40] if len(kt) else kt   # use up to 40
    # kick pitch: peak low-freq across kick windows
    f0s, ktaus = [], []
    low = lp(drums, 220)
    for t in kt[:40]:
        i=int(t*SR); seg=low[i:i+int(0.18*SR)]
        if len(seg)<512: continue
        w=seg*np.hanning(len(seg)); mag=np.abs(np.fft.rfft(w)); fr=np.fft.rfftfreq(len(seg),1/SR)
        band=(fr>=35)&(fr<=160)
        if mag[band].max()>0: f0s.append(float(fr[band][np.argmax(mag[band])]))
        ktaus.append(decay_tau(seg))
    kick_f0 = float(np.median(f0s)) if f0s else 70.0
    kick_tau = float(np.clip(np.median(ktaus) if ktaus else 0.16, 0.09, 0.32))
    # clap/snare brightness + decay
    mid = bp(drums, 1000, 9000)
    cb, ctau = [], []
    for t in ct[:40]:
        i=int(t*SR); seg=mid[i:i+int(0.10*SR)]
        if len(seg)<256: continue
        cb.append(spectral_centroid(seg)); ctau.append(decay_tau(seg))
    clap_bright = float(np.clip(np.median(cb) if cb else 3200, 1800, 6500))
    clap_tau = float(np.clip(np.median(ctau) if ctau else 0.07, 0.035, 0.13))
    # hat brightness + overall tone
    hb=[spectral_centroid(bp(drums,4000,16000)[int(t*SR):int(t*SR)+int(0.04*SR)]) for t in ht[:30] if int(t*SR)+1920<len(drums)]
    hat_bright=float(np.clip(np.median(hb) if hb else 7000, 4000, 11000))
    tone_lp=float(np.clip(hat_bright*1.35, 6500, 13500))   # brightness from the hat content, not the kick-dominated overall
    return dict(kick_f0=round(kick_f0,1), kick_tau=round(kick_tau,3),
                clap_bright=round(clap_bright,0), clap_tau=round(clap_tau,3),
                hat_bright=round(hat_bright,0), tone_lp=round(tone_lp,0))

# ---------- SHAPE the duff from the analysis ----------
def synth_dum(A, rng):
    f0=float(np.clip(A["kick_f0"],42,98))*rng.uniform(0.97,1.03); tau=A["kick_tau"]*rng.uniform(0.9,1.1)
    n=int(SR*min(0.45,tau*1.8+0.1)); t=np.arange(n)/SR
    fcurve=f0+(f0*0.45)*np.exp(-t/0.035)                     # pitch drop scaled to this kick
    ph=2*np.pi*np.cumsum(fcurve)/SR
    body=(np.sin(ph)+0.28*np.sin(2*ph)+0.1*np.sin(3*ph))*np.exp(-t/tau)
    click=lp(rng.standard_normal(n)*np.exp(-t/0.012),1000)*0.09
    x=lp(body*0.95+click, A["tone_lp"]); return (x/np.max(np.abs(x))).astype(np.float32)
def synth_clap(A, rng):
    hi=float(np.clip(A["clap_bright"],1800,6500))*rng.uniform(0.95,1.05); tau=A["clap_tau"]*rng.uniform(0.9,1.12)
    n=int(SR*max(0.12,tau*2)); t=np.arange(n)/SR
    base=bp(rng.standard_normal(n),700,hi); clap=np.zeros(n)
    for off,g in [(0.0,1.0),(0.006,0.7),(0.012,0.5),(0.02,0.3)]:
        s=int(off*SR); clap[s:]+=base[:n-s]*g
    clap*=np.exp(-t/tau); warm=np.sin(2*np.pi*210*t)*np.exp(-t/0.05)*0.22
    x=lp(clap*0.9+warm, A["tone_lp"]); return (x/np.max(np.abs(x))).astype(np.float32)
def synth_ka(A, rng):
    hi=float(np.clip(A["hat_bright"],4000,11000))*rng.uniform(0.92,1.08); n=int(SR*0.05); t=np.arange(n)/SR
    s=bp(rng.standard_normal(n),max(2000,hi*0.5),hi)*np.exp(-t/0.02)
    s[:int(SR*0.002)]*=np.linspace(0,1,int(SR*0.002)); return (s/np.max(np.abs(s))).astype(np.float32)

def main():
    P=dict(DEF); arg=sys.argv[1]
    if arg.strip().startswith("{"): P.update(json.loads(arg))
    elif os.path.exists(arg): P.update(json.load(open(arg)))
    vid=sys.argv[2]
    out=P["out"]; os.makedirs(os.path.dirname(out), exist_ok=True)
    rng=np.random.default_rng(7)
    src=load48(P["src"]); drums=load48(P["drums"]); N=len(src)
    kt,ks=onsets(drums,40,140,0.09,P["kick_thr"])
    ct,cs=onsets(drums,1200,5000,0.08,P["clap_thr"])
    ht,hs=onsets(drums,6000,13000,0.045,P["hat_thr"])
    def dd(t,s,ref,gap):
        if not len(t) or not len(ref): return t,s
        keep=[i for i,x in enumerate(t) if np.min(np.abs(ref-x))>gap]; return t[keep],s[keep]
    ct,cs=dd(ct,cs,kt,0.05); ht,hs=dd(ht,hs,ct,0.04); ht,hs=dd(ht,hs,kt,0.04)
    A=analyze(drums, kt, ct, ht)
    # POOLS of subtly-different variants (each synth call jitters pitch/decay/tone+noise) so no two hits are identical
    def pvar(shot,f):
        n=len(shot); idx=np.arange(0,n-1,f); lo=idx.astype(int); fr=idx-lo
        return (shot[lo]*(1-fr)+shot[lo+1]*fr).astype(np.float32)
    def soften(sh,ms):                       # raised-cosine attack ramp -> no sharp transient poke
        a=int(SR*ms/1000)
        if 1<a<len(sh): sh=sh.copy(); sh[:a]*=0.5-0.5*np.cos(np.linspace(0,np.pi,a))
        return sh
    DUMS=[soften(synth_dum(A,rng),3.0) for _ in range(6)]
    CLAPS=[soften(synth_clap(A,rng),2.5) for _ in range(6)]
    KAS=[soften(synth_ka(A,rng),2.0) for _ in range(8)]
    L=np.zeros(N+SR,np.float32); R=np.zeros(N+SR,np.float32)
    def place(shot,t,g,pan=0.0):
        i=int(t*SR); j=min(len(L),i+len(shot)); k=j-i
        if k<=0: return
        gl=g*np.cos((pan*0.5+0.5)*np.pi/2); gr=g*np.sin((pan*0.5+0.5)*np.pi/2); L[i:j]+=shot[:k]*gl; R[i:j]+=shot[:k]*gr
    # VELOCITY TRANSFER: each duff hit plays at the ORIGINAL drum hit's actual loudness,
    # so the groove's accents / ghost notes / build-ups match the original (not flattened).
    kE=envf(bp(drums,40,140),6); cE=envf(bp(drums,1200,5000),6); hE=envf(bp(drums,6000,13000),5)
    def vels(E,times):
        if not len(times): return np.array([])
        v=np.array([float(E[int(t*SR):int((t+0.015)*SR)].max()) if int(t*SR)<len(E) else 0.0 for t in times])
        return np.clip(v/(np.percentile(v,92)+1e-9),0,1.25)
    kv,cv,hv=vels(kE,kt),vels(cE,ct),vels(hE,ht)
    def gv(b,v): return b*(0.20+0.85*float(v))     # wide dynamic range tracking the original
    def hit(pool,t,g,pan):
        sh=pvar(pool[int(rng.integers(len(pool)))], float(rng.uniform(0.97,1.03)))  # random variant + micro-detune
        place(sh, t+float(rng.uniform(-0.004,0.004)), g*10**(rng.uniform(-1.6,1.6)/20), pan+float(rng.uniform(-0.05,0.05)))
    for t,v in zip(kt,kv): hit(DUMS,t,P["dum_gain"]*gv(1,v),0.0)
    for n_,(t,v) in enumerate(zip(ct,cv)): hit(CLAPS,t,P["clap_gain"]*gv(1,v),(0.12 if n_%2 else -0.12))
    if P["include_hats"]:
        for n_,(t,v) in enumerate(zip(ht,hv)): hit(KAS,t,P["hat_gain"]*gv(1,v),(0.3 if n_%2 else -0.3))
    st=np.stack([L[:N],R[:N]],1); st/=(np.max(np.abs(st))+1e-9); st*=0.92
    sf.write(out,st,SR,subtype="PCM_24")
    print(json.dumps({"analysis":A,"kick":len(kt),"clap":len(ct),"hat":len(ht),"out":out}))
if __name__=="__main__": main()
