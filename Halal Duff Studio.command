#!/bin/bash
# Halal Duff Studio — double-click launcher (macOS). Starts the local server if needed, waits for
# it to answer, then opens the app in your browser.
cd "$(dirname "$0")/studio" || exit 1
PY="../.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "Python environment not found (.venv). Run setup first:"
  echo "  brew install ffmpeg yt-dlp"
  echo "  python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -n1 -r -p "Press any key to close…"; exit 1
fi
if ! lsof -tiTCP:7860 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "starting Halal Duff Studio…"
  PYTORCH_ENABLE_MPS_FALLBACK=1 HF_HUB_DISABLE_TELEMETRY=1 nohup "$PY" server.py >/tmp/duffstudio.log 2>&1 &
  for i in $(seq 1 40); do curl -sf http://127.0.0.1:7860/api/config >/dev/null 2>&1 && break; sleep 0.5; done
fi
if curl -sf http://127.0.0.1:7860/api/config >/dev/null 2>&1; then
  open "http://127.0.0.1:7860"
else
  echo "The server didn't start — see /tmp/duffstudio.log"; open /tmp/duffstudio.log
fi
