#!/bin/bash
# Reproduce the selected loader and signed recovery tools in an isolated ARM root.
set -euo pipefail
[[ $EUID == 0 && -f /.omarchy-nabu-build-root && ${NABU_BOOT_MANAGER:-} == limine ]]
input=$(python3 - <<'PY'
import os,sys
sys.path.insert(0,'/build/scripts')
from nabu_paths import PROJECT,project_relative
print(PROJECT/project_relative(os.environ['NABU_LIMINE_INPUT_REL']))
PY
)
output=$(python3 /build/scripts/nabu_paths.py output)
work=$(mktemp -d /tmp/nabu-limine-install.XXXXXX)
! id nblimine >/dev/null 2>&1
useradd --system --user-group --create-home --shell /bin/bash nblimine
trap 'rm -rf "$work"; userdel -r nblimine 2>/dev/null || true' EXIT
mkdir "$work/input" "$work/local-hooks" "$work/repo-hooks" "$work/downloads"
cp /build/packages/limine/PKGBUILD "$work/"
for name in BOOTAA64.EFI limine-aarch64 source.tar.xz profile.json COPYING 3RDPARTY.md SHA256SUMS; do
  cp "$input/$name" "$work/input/"
done
chown -R nblimine:nblimine "$work"
runuser -u nblimine -- /usr/bin/bash -ec '
  cd "$1"
  export NABU_LIMINE_INPUT="$PWD/input"
  exec /usr/bin/makepkg --nodeps --noconfirm
' bash "$work"
shopt -s nullglob
local_packages=("$work"/limine-*.pkg.tar.*)
(( ${#local_packages[@]} == 1 ))
python3 /build/scripts/nabu_package_hooks.py --output "$work/local-hooks" "${local_packages[@]}"
OMARCHY_UPDATE_PACMAN=1 pacman --hookdir "$work/local-hooks" -U --needed --noconfirm "${local_packages[@]}"
# Keep signature policy intact. The image builder owns any temporary QEMU-only
# sandbox compatibility settings; this helper never changes pacman.conf.
pacman -Sw --needed --noconfirm --cachedir "$work/downloads" \
  snapper btrfs-progs limine-mkinitcpio-hook limine-snapper-sync
repo_packages=("$work"/downloads/*.pkg.tar.xz "$work"/downloads/*.pkg.tar.zst)
if (( ${#repo_packages[@]} )); then
  python3 /build/scripts/nabu_package_hooks.py --output "$work/repo-hooks" "${repo_packages[@]}"
  OMARCHY_UPDATE_PACMAN=1 pacman --hookdir "$work/repo-hooks" -U --needed --noconfirm "${repo_packages[@]}"
fi
python3 - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0,'/build/scripts')
from nabu_limine import install_snapshot_initramfs
install_snapshot_initramfs(Path('/'))
PY
mkdir -p "$output/limine-package"
cp "${local_packages[@]}" "$output/limine-package/"
pacman -Q limine limine-mkinitcpio-hook limine-snapper-sync snapper btrfs-progs > "$output/recovery-packages.lock"
