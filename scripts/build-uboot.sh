#!/bin/bash
# Host-only U-Boot build; does not access a tablet or expose USB to the builder.
set -euo pipefail
if [[ ${_NABU_UBOOT_LAUNCHER_LOADED:-} != "$0" ]]; then
  export _NABU_UBOOT_LAUNCHER_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_UBOOT_LAUNCHER_LOADED
case ${1:-} in ''|--plan) ;; *) echo 'Usage: build-uboot.sh [--plan]' >&2; exit 2;; esac
project=$(cd "$(dirname "$0")/.." && pwd)
cd "$project"
readarray -t settings < <(python3 - <<'PY'
import json,os,sys
sys.path.insert(0,'scripts')
from nabu_paths import PROJECT,project_relative
profile=json.loads((PROJECT/'profiles/nabu/uboot.json').read_text())
output=project_relative(os.environ.get('NABU_UBOOT_OUTPUT',profile['output']))
if len(output.parts)<2 or output.parts[0]!='out': raise SystemExit('U-Boot output must be below out/')
print(PROJECT/output)
print(os.environ.get('NABU_UBOOT_WORK_VOLUME',profile['work_volume']))
PY
)
[[ ${#settings[@]} == 2 ]] || exit 1
output=${settings[0]}
volume=${settings[1]}
[[ $volume =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || exit 2
if [[ ${1:-} == --plan ]]; then
  printf 'Profile: profiles/nabu/uboot.json\nSource: sources.json:uboot\nOutput: %s\nWork volume: %s\n' "$output" "$volume"
  exit 0
fi
[[ ! -f $output/SHA256SUMS ]] || { echo 'Completed U-Boot output exists; choose a fresh output.' >&2; exit 1; }
podman="$project/scripts/host-podman.sh"
image=localhost/omarchy-nabu-uboot-builder:ubuntu2404
python3 scripts/fetch-sources.py --scope bootloader
mkdir -p "$output"
if ! "$podman" image exists localhost/omarchy-nabu-kernel-builder:ubuntu2404; then
  "$podman" build --platform linux/amd64 -t localhost/omarchy-nabu-kernel-builder:ubuntu2404 \
    -f kernel/Containerfile .
fi
"$podman" build --platform linux/amd64 -t "$image" -f tools/bootloader/Containerfile .
image_id=$("$podman" image inspect "$image" --format '{{.Id}}')
[[ -z $("$podman" ps --filter "volume=$volume" --format '{{.Names}}') ]] || {
  echo 'U-Boot work volume is in use' >&2; exit 1;
}
if ! "$podman" volume exists "$volume"; then "$podman" volume create "$volume" >/dev/null; fi
jobs=${UBOOT_JOBS:-26}
[[ $jobs =~ ^[1-9][0-9]{0,2}$ ]] || exit 2
"$podman" run --rm --name omarchy-nabu-uboot-build --platform linux/amd64 \
  -v "$project:/project:z" -v "$volume:/ubootwork" \
  -e "UBOOT_JOBS=$jobs" -e "NABU_UBOOT_OUTPUT=${output#"$project/"}" \
  -e "NABU_UBOOT_IMAGE_ID=$image_id" "$image" scripts/compile-uboot.sh \
  2>&1 | tee "$output/build.log"
scripts/build-fastboot-client.sh
packaging_image=localhost/omarchy-nabu-fastboot:fedora45
packaging_id=$("$podman" image inspect "$packaging_image" --format '{{.Id}}')
"$podman" run --rm --platform linux/amd64 -v "$project:/project:z" \
  -e "NABU_UBOOT_OUTPUT=${output#"$project/"}" -e "NABU_UBOOT_PACKAGING_IMAGE_ID=$packaging_id" \
  "$packaging_image" scripts/package-uboot.sh 2>&1 | tee "$output/packaging.log"
