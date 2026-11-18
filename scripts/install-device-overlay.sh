#!/bin/bash
# Select device fixes, never import another distro's complete root overlay.
set -euo pipefail
[[ $EUID == 0 && -f /.omarchy-nabu-build-root && ${NABU_BOOT_PROFILE:-} == existing-uboot ]] || exit 1
source_tree=/build/upstream/nabu/base/overlay
while IFS= read -r path; do
  [[ -n $path && $path != \#* ]] || continue
  [[ $path != /* && $path != *..* && -f $source_tree/$path && ! -L $source_tree/$path ]] || exit 1
  mode=644
  [[ $path != usr/libexec/nabu/uki-regenerate ]] || mode=755
  install -Dm"$mode" "$source_tree/$path" "/$path"
done < /build/profiles/nabu/kumar-overlay.files
# These sources remain in use. Keep their required notices with the selected
# files, independently of product branding or optional reference documentation.
for name in nabu nabu-pkgs; do
  install -Dm644 "/build/licenses/$name-MIT.txt" "/usr/share/licenses/omarchy-nabu/$name-MIT.txt"
done
# Select the stock package's own DTB/module release, not the newest directory
# which could belong to a separately staged debug kernel.
dtb=$(readlink -f /boot/dtb-linux-nabu)
release=${dtb##*/dtb-}
[[ -f $dtb && -d /usr/lib/modules/$release && -f /boot/vmlinuz-$release ]] || exit 1
mkdir -p /etc/mkinitcpio.d /etc/kernel /etc/cmdline.d
cat > /etc/mkinitcpio.d/linux-nabu.preset <<EOF
ALL_kver="$release"
PRESETS=('default')
default_uki="/boot/efi/EFI/arch/arch-linux-nabu.efi"
default_cmdline="/etc/cmdline.d/root.conf"
EOF
printf '[UKI]\nDeviceTree=%s\n' "$dtb" > /etc/kernel/uki.conf
install -Dm644 /build/profiles/nabu/kernel.cmdline /etc/cmdline.d/root.conf
