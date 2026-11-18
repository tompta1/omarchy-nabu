#!/bin/bash
# Runs only in the disposable ARM64 builder, with /work on a Podman volume.
set -euo pipefail
if [[ ${_NABU_BUILD_SCRIPT_LOADED:-} != "$0" ]]; then
  export _NABU_BUILD_SCRIPT_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_BUILD_SCRIPT_LOADED
[[ $(uname -m) == "aarch64" && $EUID == 0 && -d /project/upstream/nabu && -d /work ]] || exit 1
umask 022
case ${1:-} in ""|--resume|--root-only) ;; *) exit 2;; esac
output=$(python3 /project/scripts/nabu_paths.py output)
if [[ -e /work/root ]]; then
  if [[ ${1:-} != "--resume" || ! -f /work/root/.omarchy-nabu-build-root ]]; then
    echo 'Work root exists. Only an interrupted build with its marker can resume; use a new volume for a fresh build.' >&2
    exit 1
  fi
fi
[[ ! -e $output/images ]] || { echo 'Output images already exist' >&2; exit 1; }
mkdir -p /work/root "$output"
root=/work/root
if [[ ! -s /work/rootfs.tar.gz ]]; then
  curl -fL --retry 3 --connect-timeout 30 \
    https://github.com/Kumar-Jy/nabu-pkgs/releases/download/repo/ArchLinuxARM-aarch64-latest.tar.gz -o /work/rootfs.tar.gz.partial
  mv /work/rootfs.tar.gz.partial /work/rootfs.tar.gz
fi
sha256sum /work/rootfs.tar.gz > "$output/bootstrap.sha256"
if [[ ! -f "$root/.omarchy-nabu-build-root" ]]; then
  bsdtar -xpf /work/rootfs.tar.gz -C "$root"
fi
mkdir -p "$root"/{proc,sys,dev,build,boot/efi}
rm -f "$root/etc/resolv.conf"
cp -L /etc/resolv.conf "$root/etc/resolv.conf"
cleanup() {
  if [[ -n ${smoke_runtime:-} ]]; then rm -rf "$root$smoke_runtime"; fi
  umount -Rl "$root/build" 2>/dev/null || true
  umount -Rl "$root/dev" 2>/dev/null || true
  umount -Rl "$root/sys" 2>/dev/null || true
  umount -l "$root/proc" 2>/dev/null || true
  umount -l "$root" 2>/dev/null || true
}
trap cleanup EXIT
mount --bind "$root" "$root"
mount -t proc proc "$root/proc"
mount --rbind /dev "$root/dev"
mount --make-rslave "$root/dev"
mount --rbind /sys "$root/sys"
mount --make-rslave "$root/sys"
mount --bind /project "$root/build"
touch "$root/.omarchy-nabu-build-root"
chroot "$root" /bin/bash /build/scripts/install-root.sh
smoke_runtime=$(mktemp -d "$root/tmp/nabu-desktop-smoke.XXXXXX")
smoke_runtime=${smoke_runtime#"$root"}
(
  ulimit -c 0
  chroot "$root" /usr/bin/env XDG_RUNTIME_DIR="$smoke_runtime" /usr/bin/Hyprland --version
  chroot "$root" /usr/bin/env XDG_RUNTIME_DIR="$smoke_runtime" /usr/bin/qs --version
  chroot "$root" /usr/local/bin/wvkbd-mobintl -v
) > "$output/desktop-smoke.log"
rm -rf "$root$smoke_runtime"
unset smoke_runtime
rm "$root/.omarchy-nabu-build-root"
cleanup
trap - EXIT
touch /work/omarchy-nabu-root-ready
if [[ ${1:-} == --root-only || ${NABU_ROOT_ONLY:-0} == 1 ]]; then
  echo 'Fresh root staged; no legacy Aloha/ESP images assembled.'
  exit 0
fi
mkdir -p "$output/images"
# Populate filesystem images from directories: no loop devices or host disks.
size_kib=$(du -sk "$root" | cut -f1)
size_mib=$((size_kib / 1024 + 4096))
truncate -s "${size_mib}M" "$output/images/rootfs.img"
mkfs.ext4 -F -L arch-root -d "$root" "$output/images/rootfs.img"
truncate -s 270M "$output/images/esp.img"
mkfs.fat -F 32 -S 4096 -s 1 -n ESPNABU "$output/images/esp.img"
mcopy -i "$output/images/esp.img" -s "$root/boot/efi/EFI" ::/
cp /project/upstream/nabu/base/overlay/opt/nabu/alohauefi.img "$output/images/"
cp /project/upstream/nabu/base/overlay/opt/nabu/fastboot-script/flash-linux.{sh,bat} "$output/"
python3 /project/scripts/verify-images.py --images "$output/images"
cd "$output"
sha256sum images/* > SHA256SUMS
echo 'Images assembled. Hardware boot and touch validation are still required.'
