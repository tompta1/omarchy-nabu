#!/bin/bash
set -euo pipefail
if command -v flatpak-spawn >/dev/null; then
  exec flatpak-spawn --host podman "$@"
fi
exec podman "$@"
