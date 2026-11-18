#!/bin/bash
set -euo pipefail
if [[ ${_NABU_KERNEL_LAUNCHER_LOADED:-} != "$0" ]]; then
  export _NABU_KERNEL_LAUNCHER_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_KERNEL_LAUNCHER_LOADED
case ${1:-} in ''|--plan) ;; *) echo 'Usage: build-debug-kernel.sh [--plan]' >&2; exit 2;; esac
project=$(cd "$(dirname "$0")/.." && pwd)
cd "$project"
export NABU_KERNEL_PROFILE=${NABU_KERNEL_PROFILE:-kernel/sources.json}
NABU_KERNEL_PROFILE=$(python3 scripts/nabu_paths.py relative "$NABU_KERNEL_PROFILE")
output=$(python3 scripts/nabu_paths.py kernel-output)
volume=$(python3 scripts/nabu_paths.py kernel-volume)
if [[ ${1:-} == --plan ]]; then
  printf 'Kernel profile: %s\nOutput: %s\nWork volume: %s\n' "$NABU_KERNEL_PROFILE" "$output" "$volume"
  exit 0
fi
[[ ! -f $output/SHA256SUMS ]] || {
  echo 'A completed kernel output already exists. Reuse it, or select a fresh NABU_KERNEL_OUTPUT.' >&2; exit 1;
}
podman="$project/scripts/host-podman.sh"
image=localhost/omarchy-nabu-kernel-builder:ubuntu2404
if (( $(df -Pk "$project" | awk 'NR==2 {print $4}') < 40 * 1024 * 1024 )); then
  echo 'At least 40 GiB free is required for a kernel build with full symbols.' >&2
  exit 1
fi
mkdir -p "$output"
output=$(cd "$output" && pwd)
"$podman" build --platform linux/amd64 -t "$image" -f "$project/kernel/Containerfile" "$project"
[[ -z $("$podman" ps --filter "volume=$volume" --format '{{.Names}}') ]] || {
  echo 'Kernel work volume is already in use' >&2; exit 1;
}
if ! "$podman" volume exists "$volume"; then "$podman" volume create "$volume" >/dev/null; fi
"$podman" run --rm --name omarchy-nabu-kernel-build --platform linux/amd64 \
  -e "KERNEL_JOBS=${KERNEL_JOBS:-8}" -e "NABU_KERNEL_PROFILE=$NABU_KERNEL_PROFILE" -e NABU_KERNEL_OUTPUT=out/kernel -v "$project:/project:z" \
  -v "$output:/project/out/kernel:z" -v "$volume:/kernelwork" "$image" \
  scripts/compile-debug-kernel.sh 2>&1 | tee "$output/build.log"
