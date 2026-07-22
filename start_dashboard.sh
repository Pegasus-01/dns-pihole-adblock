#!/usr/bin/env bash
# Run the dashboard from a local checkout (no /opt install needed).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -d "$DIR/venv" ]]; then
  echo "[*] Creating venv..."
  python3 -m venv "$DIR/venv"
fi

source "$DIR/venv/bin/activate"
pip install -q -r "$DIR/requirements.txt"

echo "[*] Dashboard -> http://localhost:8080"
python3 "$DIR/dashboard_server.py"
