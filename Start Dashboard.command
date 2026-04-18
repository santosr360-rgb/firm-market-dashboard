#!/bin/bash
cd "$(dirname "$0")"

# If the dashboard is already running, just reopen the browser tab and exit.
if lsof -ti :8765 >/dev/null 2>&1; then
  echo "Dashboard already running — opening browser..."
  open "http://localhost:8765/bloomberg-dashboard.html"
  sleep 2
  exit 0
fi

python3 server.py
