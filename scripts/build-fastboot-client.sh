#!/bin/bash
# Prepare and inventory a newer client; never connect to USB from this script.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
podman="$project/scripts/host-podman.sh"
image=localhost/omarchy-nabu-fastboot:fedora45
output=$project/out/host-tools/fastboot
mkdir -p "$output"
"$podman" build --platform linux/amd64 -t "$image" -f "$project/tools/fastboot/Containerfile" "$project"
"$podman" image inspect "$image" --format '{{.Id}}' > "$output/container-image.txt"
"$podman" run --rm --platform linux/amd64 "$image" -c \
  'fastboot --version; rpm -q android-tools; rpm -qa | LC_ALL=C sort' > "$output/client-packages.txt"
echo 'Newer fastboot client prepared. No USB device was accessed.'
