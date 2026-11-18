#!/bin/bash
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$project/out/debug"
"$project/scripts/host-podman.sh" run --rm --name omarchy-nabu-debug-image --privileged \
  --platform linux/arm64 -v "$project:/project:z" -v omarchy-nabu-work:/work \
  localhost/omarchy-nabu-builder:alpine322 scripts/assemble-debug-image.sh \
  2>&1 | tee "$project/out/debug/build.log"
