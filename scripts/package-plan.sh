#!/bin/bash
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
export OMARCHY_PATH="$project/upstream/omarchy"
# The source currently calls the editor package nvim; ARM repositories still
# publish the same editor/binary under Arch's neovim package name.
# The stable ARM snapshot provides vi through ex-vi-compat.
# Nabu's repository replaces sddm with its hardware-specific build during
# upgrades; request it directly so repeated assembly remains consistent.
"$OMARCHY_PATH/bin/omarchy-pkg-defaults" aarch64 | sed \
  -e 's/^nvim$/neovim/' -e 's/^vi$/ex-vi-compat/' -e 's/^sddm$/sddm-nabu/'
printf '%s\n' omarchy omarchy-settings omarchy-keyring
hardware_filters=(-e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d')
if [[ ${NABU_BOOT_MANAGER:-grub} == limine ]]; then
  # The stock headers only build modules for the retired stock kernel.
  hardware_filters+=(-e '/^linux-nabu-headers$/d')
fi
sed "${hardware_filters[@]}" "$project/profiles/nabu/hardware.packages"
