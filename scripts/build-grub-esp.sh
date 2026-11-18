#!/bin/bash
# Read the prepared ARM root only; assemble GRUB/ESP separately from OS images.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
cd "$project"
: "${NABU_GRUB_ROOT_VOLUME:?Select the prepared ARM root volume}"
: "${NABU_GRUB_OUTPUT:?Select a new GRUB output below out/}"
: "${NABU_ESP_OUTPUT:?Select a new ESP output below out/}"
: "${NABU_GRUB_PACKAGE:?Select the pinned signed GRUB package}"
: "${NABU_GRUB_KEYRING:?Select the public ARM signing keyring}"
[[ $NABU_GRUB_ROOT_VOLUME =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]
for key in NABU_LAYOUT NABU_GRUB_OUTPUT NABU_ESP_OUTPUT NABU_GRUB_PACKAGE NABU_GRUB_KEYRING; do
  value=$(python3 scripts/nabu_paths.py relative "${!key}")
  export "$key=$value"
done
podman="$project/scripts/host-podman.sh"
image=localhost/omarchy-nabu-grub-builder:alpine322
"$podman" build --platform linux/arm64 -t "$image" -f tools/grub/Containerfile .
"$podman" run --rm --platform linux/arm64 --privileged \
  -v "$project:/project:z" -v "$NABU_GRUB_ROOT_VOLUME:/work:ro" \
  -v "$project:/work/root/build:z" -e "NABU_LAYOUT=$NABU_LAYOUT" "$image" \
  -c 'python3 scripts/assemble-grub-candidate.py --package "/project/$1" --keyring "/project/$2" --sysroot /work/root --output "$3" && python3 scripts/assemble-esp.py --grub "/project/$3" --output "/project/$4"' \
  bash "$NABU_GRUB_PACKAGE" "$NABU_GRUB_KEYRING" "$NABU_GRUB_OUTPUT" "$NABU_ESP_OUTPUT"
