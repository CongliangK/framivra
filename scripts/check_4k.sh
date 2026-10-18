#!/usr/bin/env bash
# Compatibility entry point; keep the owning component as the implementation.
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/../apps/publish/check_4k.sh" "$@"
