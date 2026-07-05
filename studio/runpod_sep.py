"""RunPod GPU offload for Halal Duff Studio — managed on-demand pod, ALWAYS auto-terminated.

Only the two heavy separations run on the pod (demucs beat + BS-Roformer vocal). The halal duff
groove, dynamic level-match, master and video mux stay LOCAL on the Mac and are unchanged. The
pod is terminated in finally + atexit + signal handlers, so there is zero idle cost even on crash.

Config (env; the key may instead live in ~/.runpod_key):
  RUNPOD_API_KEY        or ~/.runpod_key                 (required)
  DUFF_RUNPOD_GPU       gpu_type_id     (default "NVIDIA RTX A4000")
  DUFF_RUNPOD_CLOUD     SECURE|COMMUNITY (default SECURE — guaranteed public IP for scp)
  DUFF_RUNPOD_IMAGE     base image      (default a runpod/pytorch cuda image)
  DUFF_RUNPOD_VOLUME    network volume id — if set, deps+weights persist at /workspace (fast reuse)
  DUFF_RUNPOD_DC        datacenter id for the volume path (default US-KS-2)
  DUFF_SSH_KEY          private key path (default ~/.ssh/id_ed25519)

CLI:
  python runpod_sep.py check                       # list GPUs+prices (read-only, no pod, free)
  python runpod_sep.py run <source.wav> <out_dir>  # one separation -> out/drums.wav + out/vocals.wav
API (used by engine.py when DUFF_SEP=runpod):
  separate(source_wav, out_dir) -> {"drums": <path>, "vocals": <path>}
"""
import os, sys, time, json, subprocess, atexit, signal, shlex

import runpod

STUDIO = os.path.dirname(os.path.abspath(__file__))

# ---------------- config ----------------
def _load_key():
    k = os.environ.get("RUNPOD_API_KEY")
    if not k:
        f = os.path.expanduser("~/.runpod_key")
        if os.path.exists(f):
            k = open(f).read().strip()
    if not k:
        raise RuntimeError("no RunPod API key — set RUNPOD_API_KEY or write ~/.runpod_key")
    return k

# fallback chain of cheap >=16GB GPUs — tried in order until one has capacity (RunPod availability
# fluctuates, so a single fixed GPU often fails with "no instances available")
GPU_CANDIDATES = [g.strip() for g in os.environ.get("DUFF_RUNPOD_GPU",
    "NVIDIA RTX A5000,NVIDIA RTX A4000,NVIDIA RTX A4500,NVIDIA GeForce RTX 3090,"
    "NVIDIA RTX 4000 Ada Generation,NVIDIA GeForce RTX 4090,NVIDIA A40,NVIDIA L40S"
    ).split(",") if g.strip()]
CLOUD   = os.environ.get("DUFF_RUNPOD_CLOUD", "SECURE")
IMAGE   = os.environ.get("DUFF_RUNPOD_IMAGE",
                         "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04")
VOLUME  = os.environ.get("DUFF_RUNPOD_VOLUME")            # optional network volume id
DC      = os.environ.get("DUFF_RUNPOD_DC", "US-KS-2")
SSHKEY  = os.path.expanduser(os.environ.get("DUFF_SSH_KEY", "~/.ssh/id_ed25519"))

WORKDIR  = "/workspace" if VOLUME else "/root"            # /workspace persists on a network volume
VENV     = f"{WORKDIR}/duffvenv"
MODELDIR = f"{WORKDIR}/models"                            # roformer weight (persists on volume)
TORCHHOME= f"{WORKDIR}/torch"                             # demucs weights (persists on volume)
PODPY    = f"{VENV}/bin/python"

try:
    runpod.api_key = _load_key()      # best-effort at import; may be added later via the app
except Exception:
    runpod.api_key = None             # re-loaded at point of use (_start_pod / check)

_SSH_BASE = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             "-o", "LogLevel=ERROR", "-o", "ServerAliveInterval=20", "-i", SSHKEY]

# ---------------- guaranteed pod cleanup ----------------
_LIVE_PODS = set()
def _terminate(pod_id):
    if not pod_id:
        return
    err = None
    for _ in range(4):                                    # retry — a failed terminate = open-ended billing
        try:
            runpod.terminate_pod(pod_id); _LIVE_PODS.discard(pod_id); return
        except Exception as e:
            err = e; time.sleep(2)
    print(f"[runpod] ⚠️  FAILED to terminate pod {pod_id} ({err}). Remove it now at "
          f"https://console.runpod.io/pods  (or, if you have the CLI: runpodctl remove pod {pod_id})", file=sys.stderr)
    _LIVE_PODS.discard(pod_id)

def _kill_all(*_):
    for pid in list(_LIVE_PODS):
        _terminate(pid)

atexit.register(_kill_all)
import threading

def install_signal_handlers():
    """Install SIGINT/SIGTERM pod-killers. MUST be called from the main thread (signal.signal only
    works there). The server imports runpod_sep lazily inside a worker thread, so this is called
    explicitly from server.py at startup — otherwise a SIGTERM mid-separation would skip cleanup and
    leave a pod billing until its pod-side dead-man switch fires (~30 min)."""
    if threading.current_thread() is not threading.main_thread():
        return False
    for _sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(_sig, lambda *a: (_kill_all(), os._exit(1)))
        except Exception:
            pass
    return True

install_signal_handlers()   # covers the CLI / main-thread-import case; server.py re-calls it at startup

# ---------------- helpers ----------------
def _log(m): print(f"[runpod] {m}", flush=True)

def _pubkey():
    pub = SSHKEY + ".pub"
    if not os.path.exists(pub):
        raise RuntimeError(f"no SSH public key at {pub} — generate one (ssh-keygen -t ed25519)")
    return open(pub).read().strip()

def check():
    runpod.api_key = _load_key()
    gpus = runpod.get_gpus()
    _log(f"API OK — {len(gpus)} GPU types. Candidates 16-24GB:")
    for g in sorted((x for x in gpus if 16 <= x.get("memoryInGb", 0) <= 24),
                    key=lambda x: x.get("memoryInGb", 0)):
        d = runpod.get_gpu(g["id"])
        _log(f"  {g['id']:34s} {g.get('memoryInGb')}GB  secure=${d.get('securePrice')}  community=${d.get('communityPrice')}")

def _start_pod(name):
    """Try each GPU (Secure first, then Community) until one has capacity. Secure gives a reliable
    public IP for scp; Community is a cheaper last resort."""
    runpod.api_key = _load_key()      # ensure a fresh key (may have been added after import)
    clouds = []
    for c in (CLOUD, "COMMUNITY", "SECURE"):
        if c not in clouds:
            clouds.append(c)
    last = None
    for cloud in clouds:
        for gpu in GPU_CANDIDATES:
            try:
                _log(f"trying {gpu} / {cloud}{' / volume '+VOLUME if VOLUME else ''} ...")
                kw = dict(name=name, image_name=IMAGE, gpu_type_id=gpu, gpu_count=1,
                          cloud_type=cloud, support_public_ip=True, start_ssh=True,
                          ports="22/tcp", container_disk_in_gb=25,
                          env={"PUBLIC_KEY": _pubkey()})
                if VOLUME:
                    kw.update(network_volume_id=VOLUME, volume_mount_path="/workspace")
                pod = runpod.create_pod(**kw)
                pid = (pod or {}).get("id")
                if not pid:
                    # a pod may have been created but the response is malformed — we can't track/kill
                    # it, so warn LOUDLY with the console link rather than swallowing it silently.
                    print(f"[runpod] ⚠️  create_pod returned no id ({pod!r}). If a pod was created it is "
                          f"UNTRACKED — check https://console.runpod.io/pods and remove it manually.",
                          file=sys.stderr)
                    raise RuntimeError(f"create_pod returned no id: {pod!r}")
                _LIVE_PODS.add(pid)
                _log(f"pod {pid} on {gpu}/{cloud}; waiting for SSH ...")
                return pid
            except Exception as e:
                last = e
                _log(f"  {gpu}/{cloud} unavailable: {str(e)[:110]}")
    raise RuntimeError(f"no GPU capacity right now (tried {len(GPU_CANDIDATES)} types on {clouds}). "
                       f"Try again shortly. Last error: {last}")

def _wait_ready(pod_id, budget_s=420):
    t0 = time.time()
    while time.time() - t0 < budget_s:
        try:
            info = runpod.get_pod(pod_id)
        except Exception as e:                            # transient network blip — don't kill a healthy pod
            _log(f"  (get_pod retry: {str(e)[:70]})"); time.sleep(6); continue
        rt = (info or {}).get("runtime")
        if rt and rt.get("ports"):
            for p in rt["ports"]:
                if p.get("privatePort") == 22 and p.get("isIpPublic"):
                    host, port = p["ip"], p["publicPort"]
                    _log(f"pod ready: ssh root@{host}:{port} ({int(time.time()-t0)}s)")
                    return host, port
        time.sleep(6)
    raise RuntimeError(f"pod {pod_id} never exposed public SSH within {budget_s}s")

def _ssh(host, port, cmd, check=True):
    full = ["ssh", *_SSH_BASE, "-p", str(port), f"root@{host}", cmd]
    p = subprocess.run(full, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if p.stdout: print(p.stdout, end="", flush=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"remote cmd failed (rc={p.returncode}):\n{p.stderr[-1500:]}")
    return p.stdout

def _scp(host, port, src, dst, up=True):
    a, b = (src, f"root@{host}:{dst}") if up else (f"root@{host}:{src}", dst)
    full = ["scp", *_SSH_BASE, "-P", str(port), a, b]
    p = subprocess.run(full, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"scp {'up' if up else 'down'} failed:\n{p.stderr[-1000:]}")

def _ensure_env(host, port):
    """Idempotent: build the venv + install deps + push pod_separate.py. On a network volume this
    persists (instant next time); on ephemeral disk it runs once per pod session."""
    ready = _ssh(host, port, f"test -f {VENV}/.ready && echo YES || echo NO", check=False).strip()
    _scp(host, port, os.path.join(STUDIO, "pod_separate.py"), f"{WORKDIR}/pod_separate.py")
    if ready.endswith("YES"):
        _log("pod env already provisioned (volume) — skipping install")
        return
    _log("provisioning pod env (venv + demucs + audio-separator; ~2-3 min) ...")
    setup = (
        f"set -e; mkdir -p {MODELDIR} {TORCHHOME}; "
        f"export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq ffmpeg; "
        f"python -m venv --system-site-packages {VENV}; "
        f"{VENV}/bin/pip install -q --upgrade pip; "
        # pin the roformer-relevant deps so the pod's vocal isolation matches the Mac EXACTLY
        f"{VENV}/bin/pip install -q demucs==4.0.1 audio-separator==0.44.2 onnxruntime==1.27.0 "
        f"soundfile librosa numpy scipy; "
        f"touch {VENV}/.ready; echo PROVISIONED"
    )
    _ssh(host, port, setup)

def _run_sep(host, port, remote_src, remote_out):
    _ssh(host, port, f"mkdir -p {remote_out}")
    cmd = (f"export TORCH_HOME={TORCHHOME}; export POD_MODEL_DIR={MODELDIR}; "
           f"{PODPY} {WORKDIR}/pod_separate.py {shlex.quote(remote_src)} {shlex.quote(remote_out)}")
    out = _ssh(host, port, cmd)
    drums = next((l[len("DRUMS="):].strip() for l in out.splitlines() if l.startswith("DRUMS=")), "")
    vocals= next((l[len("VOCALS="):].strip() for l in out.splitlines() if l.startswith("VOCALS=")), "")
    if not drums or not vocals:
        raise RuntimeError("pod_separate.py did not report DRUMS=/VOCALS=")
    return drums, vocals

# ---------------- public API ----------------
def _check_stem(pth):
    """A downloaded stem must be non-trivial and roughly song-length, else it's corrupt/partial."""
    import soundfile as sf
    ok = os.path.exists(pth) and os.path.getsize(pth) > 100_000
    if ok:
        try: ok = sf.info(pth).duration > 5
        except Exception: ok = False
    if not ok:
        if os.path.exists(pth): os.remove(pth)            # delete so engine won't treat it as cached
        raise RuntimeError(f"stem failed integrity check (corrupt/partial download): {pth}")

def separate(source_wav, out_dir, progress=None):
    """Full offload for one song: start pod -> separate -> download stems -> terminate (ALWAYS).
    Returns {'drums': <local path>, 'vocals': <local path>}."""
    def _p(f, m):
        _log(m)
        if progress:
            try: progress(f, m)
            except Exception: pass
    os.makedirs(out_dir, exist_ok=True)
    import shutil as _sh
    for _t in ("ssh", "scp"):                                        # the GPU path drives the pod over SSH
        if not _sh.which(_t):
            raise RuntimeError("RunPod GPU needs the OpenSSH client (ssh + scp). Windows: Settings → Apps → "
                               "Optional Features → add 'OpenSSH Client'. Linux: apt install openssh-client. "
                               "Or leave GPU off to convert locally on CPU.")
    if not os.path.exists(SSHKEY + ".pub"):
        raise RuntimeError(f"no SSH key at {SSHKEY} — create one first: ssh-keygen -t ed25519 -f \"{SSHKEY}\" -N \"\"")
    _p(0.26, "starting GPU pod…")
    pid = _start_pod("halal-duff-sep")
    try:
        host, port = _wait_ready(pid); _p(0.34, "pod ready — provisioning…")
        # Arm the pod-side dead-man switch the INSTANT SSH is up — BEFORE the ~2-3 min provisioning and
        # before any separation. So even if the Mac dies hard (laptop lid, power loss, SIGKILL, a wedged
        # wifi that hangs the SSH call) DURING setup, the pod self-destructs on its own. It removes ONLY
        # its own $RUNPOD_POD_ID (never any other pod). 30-min budget is well above the worst-case job yet
        # bounds billing if anything hangs. The brief create→SSH-up window before this arms is still
        # covered by the local finally/atexit/SIGTERM killers for all but an instant hard crash.
        _ssh(host, port, "nohup bash -lc 'sleep 1800; runpodctl remove pod $RUNPOD_POD_ID' "
                         ">/dev/null 2>&1 & disown", check=False)
        _ensure_env(host, port)
        job = f"{WORKDIR}/job"
        _ssh(host, port, f"rm -rf {job}; mkdir -p {job}/in {job}/out")
        _p(0.44, "uploading source…")
        _scp(host, port, source_wav, f"{job}/in/source.wav")
        _p(0.50, "separating on GPU (beat + vocal)…")
        r_drums, r_vocals = _run_sep(host, port, f"{job}/in/source.wav", f"{job}/out")
        local_drums = os.path.join(out_dir, "drums.wav")
        local_vocals = os.path.join(out_dir, "vocals.wav")
        _p(0.57, "downloading stems…")
        _scp(host, port, r_drums, local_drums, up=False)
        _scp(host, port, r_vocals, local_vocals, up=False)
        _check_stem(local_drums); _check_stem(local_vocals)
        _log("done — stems local; terminating pod")
        return {"drums": local_drums, "vocals": local_vocals}
    finally:
        _terminate(pid)

# ---------------- CLI ----------------
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "check":
        check()
    elif mode == "run":
        r = separate(sys.argv[2], sys.argv[3])
        print(json.dumps(r, indent=2))
    else:
        print(__doc__); sys.exit(2)
