#!/usr/bin/env bash
# Hermes Gateway — Ubuntu/Linux launcher (аналог start_gateway.bat).
# Примечание: каталог HERMES_HOME и логи — ~/.hermes/logs/gateway.log
# Первый запуск: chmod +x start_gateway.sh
set -euo pipefail
cd "$(dirname "$0")"

export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"
export PYTHONUTF8="${PYTHONUTF8:-1}"

if command -v hermes >/dev/null 2>&1; then
  exec hermes gateway run "$@"
elif [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python -m hermes_cli.main gateway run "$@"
elif [[ -x venv/bin/python ]]; then
  exec venv/bin/python -m hermes_cli.main gateway run "$@"
else
  exec python3 -m hermes_cli.main gateway run "$@"
fi
