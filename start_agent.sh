#!/usr/bin/env bash
# Hermes Agent (run_agent.py) — Ubuntu/Linux launcher (аналог start_agent.bat).
# Первый запуск: chmod +x start_agent.sh
set -euo pipefail
cd "$(dirname "$0")"

export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"
export PYTHONUTF8="${PYTHONUTF8:-1}"

if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python run_agent.py "$@"
elif [[ -x venv/bin/python ]]; then
  exec venv/bin/python run_agent.py "$@"
else
  exec python3 run_agent.py "$@"
fi
