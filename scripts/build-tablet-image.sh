#!/bin/bash
# Fresh local U-Boot/GRUB image build; no fastboot/device access.
set -euo pipefail
# Read this launcher once: editing a long-running Bash file can otherwise make
# its open file offset resume in the middle of newly written source text.
if [[ ${_NABU_LAUNCHER_LOADED:-} != "$0" ]]; then
  export _NABU_LAUNCHER_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_LAUNCHER_LOADED
case ${1:-} in ""|--audit|--resume) ;; *) echo 'Usage: build-tablet-image.sh [--audit|--resume]' >&2; exit 2;; esac
project=$(cd "$(dirname "$0")/.." && pwd)
cd "$project"
export NABU_LAYOUT=${NABU_LAYOUT:-local/tablet-layout.json}
export NABU_PROVISION=${NABU_PROVISION:-local/provision.json}
export NABU_IMAGE_OPTIONS=${NABU_IMAGE_OPTIONS:-profiles/nabu/image-options.default.json}
export NABU_KERNEL_INPUT_REL=${NABU_KERNEL_INPUT_REL:-$(python3 scripts/nabu_paths.py kernel)}
export NABU_OUTPUT_REL=${NABU_OUTPUT_REL:-out/runs/$(date -u +%Y%m%dT%H%M%SZ)-$$}
export NABU_BOOT_PROFILE=existing-uboot
export NABU_BOOT_MANAGER=${NABU_BOOT_MANAGER:-grub}
[[ $NABU_BOOT_MANAGER == grub || $NABU_BOOT_MANAGER == limine ]] || exit 2
export NABU_LIMINE_INPUT_REL=${NABU_LIMINE_INPUT_REL:-}
if [[ $NABU_BOOT_MANAGER == limine ]]; then
  if [[ -z $NABU_LIMINE_INPUT_REL ]]; then
    NABU_LIMINE_INPUT_REL=out/limine-$(python3 -c 'import json;print(json.load(open("profiles/nabu/limine.json"))["version"])')
  fi
  NABU_LIMINE_INPUT_REL=$(python3 scripts/nabu_paths.py relative "$NABU_LIMINE_INPUT_REL")
  [[ -s $NABU_LIMINE_INPUT_REL/limine-aarch64 ]] || { echo 'Run scripts/build-limine.sh first.' >&2; exit 1; }
  (cd "$NABU_LIMINE_INPUT_REL" && sha256sum -c SHA256SUMS)
fi
export NABU_SOURCE_RUN=${NABU_SOURCE_RUN:-}
NABU_LAYOUT=$(python3 scripts/nabu_paths.py relative "$NABU_LAYOUT")
NABU_PROVISION=$(python3 scripts/nabu_paths.py relative "$NABU_PROVISION")
NABU_IMAGE_OPTIONS=$(python3 scripts/nabu_paths.py relative "$NABU_IMAGE_OPTIONS")
NABU_KERNEL_INPUT_REL=$(python3 scripts/nabu_paths.py relative "$NABU_KERNEL_INPUT_REL")
NABU_OUTPUT_REL=$(python3 scripts/nabu_paths.py relative "$NABU_OUTPUT_REL")
output=$(python3 scripts/nabu_paths.py output)
[[ $NABU_OUTPUT_REL != out ]] || { echo 'Choose an isolated output below out/.' >&2; exit 1; }
python3 scripts/nabu_layout.py >/dev/null
python3 scripts/nabu_provision.py
python3 scripts/nabu_image_options.py
python3 - <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, 'scripts')
from nabu_paths import PROJECT, project_relative
data=json.loads((PROJECT/os.environ['NABU_PROVISION']).read_text())
for key in ('password_hash_file', 'authorized_keys_file'):
    if data.get(key):
        if Path(data[key]).is_absolute():
            raise SystemExit('Provisioning file references must be project-relative for container builds')
        project_relative(data[key])
PY
input=$(python3 scripts/nabu_paths.py kernel)
[[ -f $input/SHA256SUMS ]] || { echo 'Compile the selected kernel first; select its output with NABU_KERNEL_INPUT_REL.' >&2; exit 1; }
(cd "$input" && sha256sum -c SHA256SUMS)
if (( $(df -Pk "$project" | awk 'NR==2 {print $4}') < 40 * 1024 * 1024 )); then
  echo 'At least 40 GiB free is required for a fresh image build.' >&2; exit 1
fi
mkdir -p "$(dirname "$output")"
if [[ ${1:-} == --resume ]]; then
  [[ -s $output/work-volume.txt && -s $output/build-parameters.json && ! -e $output/images ]] || {
    echo 'Resume requires an interrupted run with its volume/parameter receipt and no images.' >&2; exit 1
  }
else
  mkdir "$output"
fi
exec 9>"$output/.build.lock"
flock -n 9 || { echo 'This output directory already has a running build.' >&2; exit 1; }
podman="$project/scripts/host-podman.sh"
image=localhost/omarchy-nabu-builder:alpine322
run_id=$(date -u +%Y%m%d%H%M%S)-$$
if [[ ${1:-} == --resume ]]; then
  volume=$(cat "$output/work-volume.txt")
  [[ $volume =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || { echo 'Invalid recorded build volume.' >&2; exit 1; }
  "$podman" volume exists "$volume"
else
  volume=${NABU_WORK_VOLUME:-omarchy-nabu-image-$run_id}
fi
[[ $volume =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || { echo 'Invalid build volume name.' >&2; exit 1; }
active=$("$podman" ps --filter "volume=$volume" --format '{{.Names}}')
[[ -z $active ]] || { echo 'The recorded work volume is in use by a running container.' >&2; exit 1; }
if [[ ${1:-} != --resume ]] && "$podman" volume exists "$volume"; then
  echo 'Refusing to reuse an existing work volume; choose a fresh volume.' >&2; exit 1
fi
if [[ ${1:-} != --resume ]]; then printf '%s\n' "$volume" > "$output/work-volume.txt"; fi
python3 - "${1:-}" <<'PY'
import hashlib, json, os, sys
sys.path.insert(0, 'scripts')
from nabu_paths import PROJECT, output_dir
from nabu_image_options import load_options, encoded, digest
options=load_options()
record={key:os.environ[key] for key in ('NABU_LAYOUT','NABU_PROVISION','NABU_KERNEL_INPUT_REL','NABU_IMAGE_OPTIONS','NABU_BOOT_MANAGER','NABU_LIMINE_INPUT_REL')}
record['image_options_sha256']=digest(options)
if record['NABU_BOOT_MANAGER']=='limine':
    record['limine_checksums_sha256']=hashlib.sha256((PROJECT/record['NABU_LIMINE_INPUT_REL']/'SHA256SUMS').read_bytes()).hexdigest()
record['filesystems_sha256']=hashlib.sha256((PROJECT/'profiles/nabu/filesystems.json').read_bytes()).hexdigest()
if os.environ.get('NABU_SOURCE_RUN'): record['NABU_SOURCE_RUN']=os.environ['NABU_SOURCE_RUN']
for key in ('NABU_LAYOUT','NABU_PROVISION'):
    record[key+'_sha256']=hashlib.sha256((PROJECT/record[key]).read_bytes()).hexdigest()
path=output_dir()/'build-parameters.json'
if sys.argv[1] == '--resume':
    if json.loads(path.read_text()) != record:
        raise SystemExit('Build inputs differ from the interrupted run; start a fresh run')
    if (output_dir()/'image-options.json').read_bytes() != encoded(options):
        raise SystemExit('Recorded image options differ from interrupted run; start a fresh run')
else:
    path.write_text(json.dumps(record,indent=2)+'\n')
    (output_dir()/'image-options.json').write_bytes(encoded(options))
PY
printf '%s\n' 'preparing' > "$output/build-state"
trap 'result=$?; if (( result != 0 )); then printf "failed (%s)\n" "$result" > "$output/build-state"; fi' EXIT
"$podman" build --platform linux/arm64 -t "$image" -f "$project/Containerfile" "$project"
common=(--rm --platform linux/arm64 -v "$project:/project:z"
  -e "NABU_LAYOUT=$NABU_LAYOUT" -e "NABU_PROVISION=$NABU_PROVISION"
  -e "NABU_IMAGE_OPTIONS=$NABU_OUTPUT_REL/image-options.json"
  -e "NABU_OUTPUT_REL=$NABU_OUTPUT_REL" -e "NABU_KERNEL_INPUT_REL=$NABU_KERNEL_INPUT_REL"
  -e NABU_BOOT_PROFILE=existing-uboot -e "NABU_BOOT_MANAGER=$NABU_BOOT_MANAGER" -e "NABU_LIMINE_INPUT_REL=$NABU_LIMINE_INPUT_REL" -e NABU_ROOT_ONLY=1 -e NABU_NATIVE_IMAGE_RECEIPT=1)
"$podman" run "${common[@]}" "$image" -c 'git config --global --add safe.directory "*"; python3 scripts/fetch-sources.py'
"$podman" run "${common[@]}" "$image" -c 'python3 scripts/audit-packages.py'
if [[ ${1:-} == --audit ]]; then
  printf '%s\n' 'audit-only' > "$output/build-state"
  exit 0
fi
if [[ ${1:-} != --resume ]]; then "$podman" volume create "$volume" >/dev/null; fi
if [[ ${1:-} != --resume && -n $NABU_SOURCE_RUN ]]; then
  source_volume=$(python3 scripts/reuse-prepared-root.py)
  [[ -z $("$podman" ps --filter "volume=$source_volume" --format '{{.Names}}') ]] || {
    echo 'Prepared-root volume is still in use' >&2; exit 1;
  }
  "$podman" run --rm --platform linux/arm64 --privileged \
    -v "$source_volume:/source:ro" -v "$volume:/work" "$image" -ec \
    '[[ -f /source/omarchy-nabu-root-ready && -d /source/root ]]
     [[ ! -e /work/root ]]
     cp -a --reflink=auto /source/root /work/root
     touch /work/omarchy-nabu-root-ready'
fi
printf '%s\n' 'building' > "$output/build-state"
"$podman" run "${common[@]}" -e "NABU_RESUME=${1:-}" --name "omarchy-nabu-image-$run_id" --privileged \
  -v "$volume:/work" "$image" -c \
  'if [[ ! -f /work/omarchy-nabu-root-ready ]]; then
     if [[ $NABU_RESUME == --resume ]]; then scripts/build-rootfs.sh --resume; else scripts/build-rootfs.sh --root-only; fi
   fi && scripts/assemble-tablet-install.sh' \
  2>&1 | tee -a "$output/build.log"
# Filesystem creation/inspection does not execute ARM binaries. Run it natively
# with modern Btrfs tools rather than the old emulated formatter.
fs_image=localhost/omarchy-nabu-filesystem-tools:arch
"$podman" build --platform linux/amd64 -t "$fs_image" -f "$project/tools/filesystems/Containerfile" "$project"
"$podman" run "${common[@]:3}" --rm --platform linux/amd64 -v "$volume:/work" "$fs_image" \
  -c 'bash scripts/assemble-filesystem-images.sh' 2>&1 | tee -a "$output/build.log"
# Hash large artifacts with the native host CPU, after ARM filesystem/UKI checks.
# The receipt creator performs the same layout/hash validation on either arch.
python3 scripts/nabu_images.py create
(cd "$output" && sha256sum images/* image-receipt.json > SHA256SUMS)
printf '%s\n' 'offline-verified; hardware untested; redistribution blocked pending license review' > "$output/build-state"
echo "Local build complete: $NABU_OUTPUT_REL"
