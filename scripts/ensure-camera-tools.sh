#!/bin/bash
# Image-build only: fresh roots already include this; prepared roots may not.
set -euo pipefail
[[ $EUID == 0 && -f /.omarchy-nabu-build-root ]] || exit 1
if pacman -Q libcamera-tools >/dev/null 2>&1; then
  command -v cam >/dev/null
  exit 0
fi
installed=$(pacman -Q libcamera | awk '{print $2}')
available=$(LC_ALL=C pacman -Si extra/libcamera-tools | awk '/^Version/{print $3}')
[[ -n $installed && $installed == "$available" ]] || {
  echo 'Prepared root libcamera differs from available tools; rebuild the OS base together.' >&2
  exit 1
}
work=$(mktemp -d /tmp/omarchy-nabu-camera-tools.XXXXXX)
cleanup() {
  gpgconf --homedir "$work/keyring" --kill all 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT
mkdir -m700 "$work/keyring"
# Prepared images carry no initialized machine keyring. Use a temporary trusted
# Arch ARM keyring for this one matching package; leave runtime trust untouched.
pacman-key --gpgdir "$work/keyring" --init
pacman-key --gpgdir "$work/keyring" --populate archlinuxarm
sed -e '/^[[:space:]]*GPGDir[[:space:]]*=/d' \
    -e "/^\[options\]/a GPGDir = $work/keyring\nDisableSandboxFilesystem\nDisableSandboxSyscalls" \
    /etc/pacman.conf > "$work/pacman.conf"
# A small reuse repair must not silently upgrade the prepared OS or its libs.
transaction=$(LC_ALL=C pacman --config "$work/pacman.conf" -Sp --needed \
  --print-format '%n %v' extra/libcamera-tools)
[[ $transaction == "libcamera-tools $installed" ]] || {
  echo 'Camera tools need other package changes; rebuild the OS base together.' >&2
  exit 1
}
pacman --config "$work/pacman.conf" -S --needed --noconfirm extra/libcamera-tools
command -v cam >/dev/null
