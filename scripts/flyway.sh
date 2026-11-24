#!/usr/bin/env bash
# Local wrapper; database credentials never leave this computer as configuration.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "$root/.venv/Scripts/python.exe" ]]; then
  python="$root/.venv/Scripts/python.exe"
elif [[ -x "$root/.venv/bin/python" ]]; then
  python="$root/.venv/bin/python"
else
  python=python
fi
exec "$python" "$root/scripts/database.py" "${@:-info}"
