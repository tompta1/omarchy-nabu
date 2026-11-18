#!/bin/bash
# Populate images from an isolated build root; never move its /boot away.
set -euo pipefail
if [[ ${_NABU_BUILD_SCRIPT_LOADED:-} != "$0" ]]; then
  export _NABU_BUILD_SCRIPT_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_BUILD_SCRIPT_LOADED
root=/work/root
project=/project
[[ $(uname -m) == aarch64 && $EUID == 0 && -f /work/omarchy-nabu-root-ready ]] || {
  echo 'A fresh completed root build is required; historical work volumes are not assembly inputs.' >&2
  exit 1
}
umask 022
output=$(python3 "$project/scripts/nabu_paths.py" output)
images=$output/images
[[ ! -e $images ]] || { echo 'Output images already exist; choose a new output directory.' >&2; exit 1; }
python3 "$project/scripts/nabu_layout.py" >/dev/null
python3 "$project/scripts/nabu_provision.py" >/dev/null
export NABU_BOOT_PROFILE=existing-uboot
manager=${NABU_BOOT_MANAGER:-grub}
[[ $manager == grub || $manager == limine ]] || { echo 'Unsupported boot manager' >&2; exit 1; }
rm -f /work/omarchy-nabu-image-prepared
created_marker=0
cleanup() {
  for path in build dev sys boot; do umount -Rl "$root/$path" 2>/dev/null || true; done
  umount -l "$root/proc" 2>/dev/null || true
  umount -l "$root" 2>/dev/null || true
}
finish() {
  cleanup
  if (( created_marker )); then rm -f "$root/.omarchy-nabu-build-root"; fi
}
trap finish EXIT
mount --bind "$root" "$root"
mount --bind "$root/boot" "$root/boot"
mount -t proc proc "$root/proc"
mount --rbind /dev "$root/dev"
mount --make-rslave "$root/dev"
mount --rbind /sys "$root/sys"
mount --make-rslave "$root/sys"
mount --bind "$project" "$root/build"
touch "$root/.omarchy-nabu-build-root"
created_marker=1
python3 "$project/scripts/run-with-build-dns.py" --root "$root" -- \
  chroot "$root" /bin/bash /build/scripts/configure-tablet-install-root.sh
chroot "$root" /bin/bash /build/scripts/install-debug-kernel-root.sh
rm "$root/.omarchy-nabu-build-root"
created_marker=0
# Prepared roots may have reacquired a package-signing keyring during isolated
# tool qualification. It is build state, not a device identity or image payload.
rm -rf "$root/etc/pacman.d/gnupg"
rm -f "$root/var/lib/dbus/machine-id"
cleanup
touch /work/omarchy-nabu-image-prepared
echo 'ARM root/UKI preparation complete; assemble filesystem images with native tools.'
