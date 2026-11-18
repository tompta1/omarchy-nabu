#!/bin/bash
# Target-side assembly only. Never run against the Distrobox or Bazzite root.
set -euo pipefail
if [[ ${_NABU_BUILD_SCRIPT_LOADED:-} != "$0" ]]; then
  export _NABU_BUILD_SCRIPT_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_BUILD_SCRIPT_LOADED
[[ $(uname -m) == "aarch64" && -f /etc/arch-release && -f /.omarchy-nabu-build-root && -d /build/upstream/nabu ]] || exit 1
# The outer builder validated this relative path. The minimal bootstrap has no
# Python until the package plan is installed, so use Bash during early setup.
output=/build/${NABU_OUTPUT_REL:-out}
mkdir -p "$output"
export OMARCHY_PATH=/usr/share/omarchy
export PATH="$OMARCHY_PATH/bin:$PATH"
source_tree=/build/upstream/omarchy
pacman-key --init
pacman-key --populate archlinuxarm
for key in "$source_tree"/default/pacman/keys/*.asc; do
  [[ -f $key ]] || continue
  pacman-key --add "$key"
done
pacman-key --lsign-key 68B3537F39A313B3E574D06777193F152BDBE6A6
# Match upstream omarchy-update-keyring: obtain the packaging key through
# Omarchy's WKD and trust only the published full fingerprint.
(
  GNUPGHOME=$(mktemp -d)
  export GNUPGHOME
  trap 'gpgconf --kill all 2>/dev/null || true; rm -rf "$GNUPGHOME"' EXIT
  gpg --batch --quiet --auto-key-locate clear,nodefault,wkd --locate-external-keys pkgs@omarchy.org
  gpg --batch --export 40DFB630FF42BCFFB047046CF0134EE680CAC571 > "$GNUPGHOME/omarchy.gpg"
  pacman-key --add "$GNUPGHOME/omarchy.gpg"
)
pacman-key --lsign-key 40DFB630FF42BCFFB047046CF0134EE680CAC571
cp "$source_tree/default/pacman/aarch64/pacman-stable.conf" /etc/pacman.conf
# Pacman's Landlock/seccomp sandbox is unavailable under qemu-user in a
# rootless container. These settings apply to this image-building chroot.
sed -i '/^\[options\]/a DisableSandboxFilesystem\nDisableSandboxSyscalls' /etc/pacman.conf
cp "$source_tree/default/pacman/aarch64/mirrorlist-stable" /etc/pacman.d/mirrorlist
# General applications follow Omarchy/Arch. Only explicitly qualified hardware
# requests override those repos. Holds are applied after initial installation.
install -Dm644 /build/overlay/etc/pacman.d/nabu.conf /etc/pacman.d/nabu.conf
printf '\nInclude = /etc/pacman.d/nabu.conf\n' >> /etc/pacman.conf
if pacman -Q linux-aarch64 >/dev/null 2>&1; then pacman -R --noconfirm linux-aarch64; fi
# Force DB refresh: the bootstrap may cache another channel with identical
# database timestamps but different package hashes/sizes.
pacman -Syyu --noconfirm
mapfile -t packages < <(bash /build/scripts/package-plan.sh | sort -u)
# Stable ARM publishes the traditional vi implementation as ex-vi-compat.
mkdir -p /boot/efi/EFI/arch
pacman -S --needed --noconfirm "${packages[@]}"
bash /build/scripts/build-device-packages.sh
bash /build/scripts/build-keyboard.sh
# Use plain Omarchy's packaged runtime and skel. The source pin defines the
# package plan; record the actual signed runtime package version below.
if id alarm >/dev/null 2>&1; then userdel -r alarm; fi
if ! id tablet >/dev/null 2>&1; then
  useradd -m -G wheel,audio,video,input -s /bin/bash tablet
fi
passwd -l tablet
passwd -l root
printf '%%wheel ALL=(ALL:ALL) ALL\n' > /etc/sudoers.d/10-wheel
chmod 440 /etc/sudoers.d/10-wheel
sed -i 's/^#en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
locale-gen
printf 'LANG=en_US.UTF-8\n' > /etc/locale.conf
printf 'omarchy-nabu\n' > /etc/hostname
# Existing U-Boot uses a reviewed device-file allowlist and our boot profile.
# Keep the older complete overlay confined to the historical Aloha build path.
if [[ ${NABU_BOOT_PROFILE:-legacy} == existing-uboot ]]; then
  bash /build/scripts/install-device-overlay.sh
else
  cp -a /build/upstream/nabu/base/overlay/. /
  bash /overlay-post-apply
  rm /overlay-post-apply
fi
mkdir -p /boot/efi/EFI/arch
if [[ ${NABU_BOOT_PROFILE:-legacy} != existing-uboot ]]; then
  cp -a /opt/nabu/efi-template/. /boot/efi/
fi
bash /build/scripts/apply-root-overlay.sh /build/overlay /
python3 /build/scripts/install-license-notices.py
install -Dm755 /build/scripts/nabu_repositories.py /usr/local/libexec/omarchy-nabu-repositories
install -Dm644 /build/profiles/nabu/repositories.json /usr/share/omarchy-nabu/repositories.json
install -Dm755 /build/scripts/omarchy-nabu-pacman-sandbox /usr/local/libexec/omarchy-nabu-pacman-sandbox
/usr/local/libexec/omarchy-nabu-pacman-restore
systemctl enable omarchy-nabu-pacman-sandbox.service
install -Dm755 /build/scripts/omarchy-nabu-keyboard /usr/local/bin/omarchy-nabu-keyboard
install -Dm755 /build/scripts/omarchy-nabu-iris-recover /usr/local/bin/omarchy-nabu-iris-recover
install -Dm755 /build/scripts/omarchy-nabu-autorotate /usr/local/bin/omarchy-nabu-autorotate
install -Dm755 /build/scripts/omarchy-nabu-wifi-identity /usr/local/libexec/omarchy-nabu-wifi-identity
python3 /build/scripts/nabu_desktop.py
/usr/local/libexec/omarchy-nabu-shell-compat
systemctl --global enable omarchy-nabu-autorotate.service nabu-autobrightness.service
mkdir -p /home/tablet/.local/share/applications
cat > /home/tablet/.local/share/applications/omarchy-nabu-keyboard.desktop <<'EOF'
[Desktop Entry]
Name=On-screen keyboard
Exec=omarchy-nabu-keyboard
Type=Application
Icon=input-keyboard
Categories=Utility;Accessibility;
EOF
chown -R tablet:tablet /home/tablet
install -Dm644 /home/tablet/.local/share/applications/omarchy-nabu-keyboard.desktop \
  /usr/local/share/applications/omarchy-nabu-keyboard.desktop
mkdir -p /home/tablet/.config/omarchy/extensions
install -m644 /build/profiles/nabu/omarchy-menu.json \
  /home/tablet/.config/omarchy/extensions/omarchy-menu.jsonc
chown -R tablet:tablet /home/tablet/.config/omarchy/extensions
# Nabu uses NetworkManager and its Qualcomm services; no laptop hardware setup.
systemctl enable NetworkManager bluetooth tqftpserv rmtfs nabu-pmac sddm omarchy-nabu-keyring \
  systemd-resolved systemd-timesyncd power-profiles-daemon omarchy-nabu-wifi-shutdown
for unit in systemd-networkd.service systemd-networkd.socket \
  systemd-networkd-varlink.socket systemd-networkd-varlink-metrics.socket \
  systemd-networkd-resolve-hook.socket; do
  systemctl disable "$unit" 2>/dev/null || true
done
systemctl mask systemd-networkd-wait-online.service NetworkManager-wait-online.service
for unit in qrtr-ns hexagonrpcd; do
  if [[ -f /usr/lib/systemd/system/$unit.service ]]; then systemctl enable "$unit"; fi
done
mkdir -p /etc/sddm.conf.d
# Login is locked until the explicit local provisioning stage runs.
printf '[Users]\nRememberLastUser=false\n' > /etc/sddm.conf.d/90-nabu.conf
if [[ ${NABU_BOOT_MANAGER:-grub} == limine ]]; then
  bash /build/scripts/install-snapshot-tools.sh
else
  # Historical boot profile owns UKI generation instead of generic Limine hooks.
  mkdir -p /etc/pacman.d/hooks
  for hook in /usr/share/libalpm/hooks/*limine* /usr/share/libalpm/hooks/*snapper*; do
    [[ ! -e $hook ]] || ln -sf /dev/null "/etc/pacman.d/hooks/${hook##*/}"
  done
fi
/usr/bin/mkinitcpio -P 2>&1 | tee "$output/initramfs-base.log"
test -s /boot/efi/EFI/arch/arch-linux-nabu.efi
if [[ ${NABU_BOOT_PROFILE:-legacy} != existing-uboot ]]; then tar czf /opt/nabu/efi.tgz -C /boot/efi .; fi
pacman -Q > "$output/packages.lock"
python3 /build/scripts/nabu_sources.py --file /build/sources.json --scope os > "$output/sources.json"
rm -f /etc/ssh/ssh_host_* /var/lib/dbus/machine-id
: > /etc/machine-id
rm -rf /var/cache/pacman/pkg/*
gpgconf --homedir /etc/pacman.d/gnupg --kill all || true
rm -rf /etc/pacman.d/gnupg
# Remove builder-only settings. The generated filesystem compatibility include
# is reselected by the native kernel on first boot, including stock fallback.
sed -i '/^DisableSandboxSyscalls$/d; /^DisableSandboxFilesystem$/d' /etc/pacman.conf
ln -sfn /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
