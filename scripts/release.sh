#!/usr/bin/env bash
# GitHub release transaction: validate tagged commit → draft assets → verify → publish.
# --dry-run performs local identity checks only; real publishing requires gh auth.
set -euo pipefail
REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$REPO/scripts/release_github.py" "$@"
