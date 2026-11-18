#!/bin/bash
set -euo pipefail
case ${1:-} in
  ""|--audit|--resume) ;;
  *) echo 'Usage: scripts/build.sh [--audit|--resume]' >&2; exit 2;;
esac
project=$(cd "$(dirname "$0")/.." && pwd)
cd "$project"
if (( $(df -Pk "$project" | awk 'NR==2 {print $4}') < 30 * 1024 * 1024 )); then
  echo 'At least 30 GiB free workspace storage is required.' >&2
  exit 1
fi
mkdir -p out
podman="$project/scripts/host-podman.sh"
image=localhost/omarchy-nabu-builder:alpine322
"$podman" build --platform linux/arm64 -t "$image" -f "$project/Containerfile" "$project"
"$podman" run --rm --platform linux/arm64 -v "$project:/project:z" "$image" \
  -c 'git config --global --add safe.directory "*"; python3 scripts/fetch-sources.py'
"$podman" run --rm --platform linux/arm64 -v "$project:/project:z" "$image" -c 'python3 scripts/audit-packages.py'
if [[ ${1:-} == "--audit" ]]; then
  exit 0
fi
volume=${NABU_WORK_VOLUME:-omarchy-nabu-work}
"$podman" volume create "$volume" >/dev/null
# Rootless privileged permits nested proc/dev mounts in the build namespace.
# No host device or block-device passthrough is used.
"$podman" run --rm --name omarchy-nabu-build --privileged --platform linux/arm64 \
  -v "$project:/project:z" -v "$volume:/work" "$image" scripts/build-rootfs.sh "${@}" 2>&1 | tee out/build.log
