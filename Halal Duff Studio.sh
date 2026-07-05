#!/bin/bash
# Halal Duff Studio — Linux launcher. Starts the local server if needed, waits for it, opens the app.
cd "$(dirname "$0")/studio" || exit 1
PY="../.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "Python environment not found (.venv). Run setup first:"
  echo "  sudo apt install ffmpeg   # + a yt-dlp binary on PATH"
  echo "  python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -n1 -r -p "Press any key to close…"; exit 1
fi
up(){ "$PY" -c "import socket,sys;s=socket.socket();s.settimeout(1);sys.exit(0 if s.connect_ex(('127.0.0.1',7860))==0 else 1)" 2>/dev/null; }
if ! up; then
  echo "starting Halal Duff Studio…"
  HF_HUB_DISABLE_TELEMETRY=1 nohup "$PY" server.py >/tmp/duffstudio.log 2>&1 &
  for i in $(seq 1 60); do up && break; sleep 0.5; done
fi
if up; then
  xdg-open "http://127.0.0.1:7860/?t=$(date +%s)" >/dev/null 2>&1 || echo "Open http://127.0.0.1:7860 in your browser"
else
  echo "The server didn't start — see /tmp/duffstudio.log"
fi
