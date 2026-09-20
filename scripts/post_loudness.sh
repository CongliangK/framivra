#!/usr/bin/env bash
# Compatibility entrypoint; Windows: python scripts/post_loudness.py PROJECT
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${PYTHON:-}" ]; then
  exec "$PYTHON" "$SCRIPT_DIR/post_loudness.py" "$@"
fi
exec env python3 "$SCRIPT_DIR/post_loudness.py" "$@"
