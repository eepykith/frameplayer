#!/bin/sh
# Run Frame Player from a local checkout.
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
if [ -x "$root/.venv/bin/python" ]; then
  exec "$root/.venv/bin/python" -m frameplayer "$@"
fi
exec python3 -m frameplayer "$@"
