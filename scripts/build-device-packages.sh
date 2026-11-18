#!/bin/bash
# Runs only inside the disposable Arch ARM build root, never on the tablet.
set -euo pipefail
if [[ ${_NABU_DEVICE_BUILDER_LOADED:-} != "$0" ]]; then
  export _NABU_DEVICE_BUILDER_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_DEVICE_BUILDER_LOADED
[[ $EUID == 0 && $(uname -m) == aarch64 && -f /.omarchy-nabu-build-root ]] || exit 1
output=$(python3 /build/scripts/nabu_paths.py output)
mkdir -p "$output/device-packages"
pacman -S --needed --noconfirm base-devel meson ninja protobuf python-protobuf \
  gobject-introspection libqmi protobuf-c libgudev polkit
user=nbupkgbuild
! id "$user" >/dev/null 2>&1 || { echo 'Package build user already exists' >&2; exit 1; }
useradd --system --user-group --create-home --shell /bin/bash "$user"
work=$(mktemp -d /tmp/nabu-device-packages.XXXXXX)
trap 'rm -rf "$work"; userdel -r "$user" 2>/dev/null || true' EXIT
chmod 755 "$work"
cp /etc/makepkg.conf "$work/makepkg.conf"
printf '\nPKGEXT=".pkg.tar.zst"\n' >> "$work/makepkg.conf"
cp /build/profiles/nabu/device-layer.json "$output/device-packages/device-layer.json"
mapfile -t source_packages < <(python3 -c \
  'import json; print(*json.load(open("/build/profiles/nabu/repositories.json"))["source_packages"], sep="\n")')
for package in "${source_packages[@]}"; do
  cp -a "/build/packages/$package" "$work/$package"
  chown -R "$user:$user" "$work/$package"
  cp "$work/$package/PKGBUILD" "$output/device-packages/$package.PKGBUILD"
  runuser -u "$user" -- bash -ec 'cd "$1"; makepkg --printsrcinfo' bash "$work/$package" \
    > "$output/device-packages/$package.SRCINFO"
  runuser -u "$user" -- bash -ec 'cd "$1"; makepkg --config "$2" --noconfirm' bash "$work/$package" "$work/makepkg.conf" \
    2>&1 | tee "$output/device-packages/$package.log"
  artifacts=("$work/$package"/"$package"-[0-9]*.pkg.tar.zst)
  [[ ${#artifacts[@]} == 1 && -f ${artifacts[0]} ]] || exit 1
  cp "${artifacts[0]}" "$output/device-packages/"
  pacman -U --noconfirm "${artifacts[0]}"
done
(cd "$output/device-packages" && sha256sum ./*.pkg.tar.zst > SHA256SUMS)
python3 - <<'PY'
import hashlib, json, subprocess
from pathlib import Path
policy=json.loads(Path('/build/profiles/nabu/repositories.json').read_text())
packages={name:subprocess.check_output(['pacman','-Q',name],text=True).split()[1]
          for name in policy['source_packages']}
receipt={'schema':1,'packages':packages,
         'recipes':{name:hashlib.sha256(Path('/build/packages',name,'PKGBUILD').read_bytes()).hexdigest()
                    for name in packages}}
destination=Path('/usr/share/omarchy-nabu/source-packages.json')
destination.parent.mkdir(parents=True,exist_ok=True)
destination.write_text(json.dumps(receipt,indent=2)+'\n')
PY
