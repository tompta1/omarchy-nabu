#!/bin/bash
# Package an already compiled kernel without installing or selecting it.
set -euo pipefail
if [[ ${_NABU_PACKAGE_BUILDER_LOADED:-} != "$0" ]]; then
  export _NABU_PACKAGE_BUILDER_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_PACKAGE_BUILDER_LOADED
[[ $EUID == 0 && $(uname -m) == aarch64 && -f /.omarchy-nabu-build-root ]] || exit 1
input=$(python3 /build/scripts/nabu_paths.py kernel)
output=$(python3 /build/scripts/nabu_paths.py output)
slot=${NABU_KERNEL_SLOT:-debug-next}
case $slot in local|debug|debug-next|debug-pm) ;; *) exit 2;; esac
(cd "$input" && sha256sum -c SHA256SUMS)
mkdir -p "$output/kernel-package"
user=nbukpkgbuild
! id "$user" >/dev/null 2>&1 || exit 1
useradd --system --user-group --create-home --shell /bin/bash "$user"
work=$(mktemp -d /tmp/nabu-kernel-package.XXXXXX)
trap 'rm -rf "$work"; userdel -r "$user" 2>/dev/null || true' EXIT
cp /build/kernel/PKGBUILD "$work/"
cp /etc/makepkg.conf "$work/makepkg.conf"
printf '\nPKGEXT=".pkg.tar.zst"\n' >> "$work/makepkg.conf"
# Keep package inputs accessible to the unprivileged builder even when CI
# output ancestors are mode 0700. Never change permissions on private outputs.
mkdir "$work/input"
for artifact in kernelrelease Image Image.gz sm8150-xiaomi-nabu.dtb modules.tar config; do
  cp "$input/$artifact" "$work/input/"
done
if grep -qx CONFIG_EFI_ZBOOT=y "$input/config"; then
  cp "$input/vmlinuz.efi" "$work/input/"
fi
chown -R "$user:$user" "$work"
runuser -u "$user" -- env NABU_KERNEL_INPUT="$work/input" NABU_KERNEL_SLOT="$slot" \
  NABU_KERNEL_UKI_DIR=/boot/EFI/arch bash -ec \
  'cd "$1"; makepkg --nodeps --noconfirm --config "$1/makepkg.conf"' bash "$work"
packages=("$work/linux-nabu-$slot"-[0-9]*.pkg.tar.zst)
[[ ${#packages[@]} == 1 && -f ${packages[0]} ]] || exit 1
cp "${packages[0]}" "$output/kernel-package/"
(cd "$output/kernel-package" && sha256sum ./*.pkg.tar.zst > SHA256SUMS)
printf 'Kernel package built in slot %s. Nothing installed or selected.\n' "$slot"
