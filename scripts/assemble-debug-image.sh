#!/bin/bash
set -euo pipefail
input=$(python3 /project/scripts/nabu_paths.py kernel)
[[ -d /work/root && -f $input/SHA256SUMS ]] || exit 1
[[ ! -e /project/out/debug/images ]] || { echo 'Debug images already exist' >&2; exit 1; }
mkdir -p /project/out/debug
root=/work/root
cleanup() {
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
chroot "$root" /bin/bash /build/scripts/install-debug-kernel-root.sh
rm "$root/.omarchy-nabu-build-root"
cleanup
trap - EXIT
images=/project/out/debug/images
mkdir -p "$images"
size_kib=$(du -sk "$root" | cut -f1)
truncate -s "$((size_kib / 1024 + 4096))M" "$images/rootfs.img"
mkfs.ext4 -F -L arch-root -d "$root" "$images/rootfs.img"
truncate -s 270M "$images/esp.img"
mkfs.fat -F 32 -S 4096 -s 1 -n ESPNABU "$images/esp.img"
mcopy -i "$images/esp.img" -s "$root/boot/efi/EFI" ::/
cp /project/out/images/alohauefi.img "$images/"
cp /project/out/flash-linux.{sh,bat} /project/out/debug/
python3 /project/scripts/verify-images.py --images "$images" --debug
cd /project/out/debug
sha256sum images/* > SHA256SUMS
echo 'Debug image assembled; no tablet was booted or flashed.'
