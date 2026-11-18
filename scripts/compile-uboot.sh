#!/bin/bash
set -euo pipefail
if [[ ${_NABU_UBOOT_COMPILER_LOADED:-} != "$0" ]]; then
  export _NABU_UBOOT_COMPILER_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_UBOOT_COMPILER_LOADED
[[ -d /ubootwork && -d /project/profiles/nabu ]] || exit 1
cd /project
readarray -t config < <(python3 - <<'PY'
import json,os,sys
sys.path.insert(0,'scripts')
from nabu_paths import PROJECT,project_relative
from nabu_sources import load_sources
p=json.loads((PROJECT/'profiles/nabu/uboot.json').read_text())
s=load_sources(PROJECT/'sources.json')[p['source']]
assert s['scope']=='bootloader'
assert (p['android_header_version'],p['android_base'],p['android_page_size'])==(0,0,4096)
relative=project_relative(os.environ['NABU_UBOOT_OUTPUT'])
assert len(relative.parts)>1 and relative.parts[0]=='out'
print(PROJECT/'upstream'/p['source']); print(s['commit'])
print(PROJECT/relative)
print(p['device_tree']); print(p['localversion'])
print(' '.join(p['make_configs'])); print(p['version'])
PY
)
[[ ${#config[@]} == 7 ]] || exit 1
source=${config[0]}
commit=${config[1]}
output=${config[2]}
export DEVICE_TREE=${config[3]}
export LOCALVERSION=${config[4]}
read -r -a make_configs <<< "${config[5]}"
expected_version=${config[6]}
build_id=${NABU_UBOOT_IMAGE_ID#sha256:}
[[ $build_id =~ ^[0-9a-f]{64}$ ]] || exit 2
# Host objects cannot be reused across compiler/libcrypto/container changes.
# Retain each toolchain's work independently instead of mixing cached objects.
build=/ubootwork/build-$build_id
git config --global --add safe.directory "$source"
[[ $(git -C "$source" rev-parse HEAD) == "$commit" && -z $(git -C "$source" status --porcelain) ]] || exit 1
mkdir -p "$build" "$output"
export CROSS_COMPILE=aarch64-linux-gnu-
export KBUILD_BUILD_USER=nabu KBUILD_BUILD_HOST=omarchy-nabu-builder
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git -C "$source" show -s --format=%ct HEAD)
make -C "$source" O="$build" "${make_configs[@]}"
make -C "$source" O="$build" -j"${UBOOT_JOBS:-26}"
python3 - "$build/.config" <<'PY'
from pathlib import Path
import sys
config=Path(sys.argv[1]).read_text().splitlines()
for required in ('CONFIG_ARM64=y','CONFIG_EFI_LOADER=y','CONFIG_CONSOLE_RECORD=y',
                 'CONFIG_USB_FUNCTION_FASTBOOT=y','CONFIG_EFI_CAPSULE_ON_DISK=y'):
    if required not in config: raise SystemExit('Required U-Boot setting missing: '+required)
PY
fdtget -t s "$build/u-boot.dtb" / compatible | grep -qw xiaomi,nabu
cp "$build/u-boot" "$build/u-boot-nodtb.bin" "$build/u-boot.dtb" "$output/"
cp "$build/.config" "$output/config"
cp "$build/include/config/uboot.release" "$output/uboot.release"
grep -q "^$expected_version" "$output/uboot.release"
gzip -n -c "$output/u-boot-nodtb.bin" > "$output/u-boot.gz"
cat "$output/u-boot.dtb" >> "$output/u-boot.gz"
"${CROSS_COMPILE}readelf" -h "$output/u-boot" > "$output/elf-header.txt"
grep -q AArch64 "$output/elf-header.txt"
"${CROSS_COMPILE}gcc" --version > "$output/compiler.txt"
dpkg-query -W > "$output/build-packages.txt"
git -C "$source" diff --exit-code
python3 - "$output" <<'PY'
import hashlib,json,os,subprocess,sys
from pathlib import Path
output=Path(sys.argv[1])
profile=Path('profiles/nabu/uboot.json')
p=json.loads(profile.read_text()); pin=json.loads(Path('sources.json').read_text())[p['source']]
record={'schema':1,'component':'U-Boot for Xiaomi Nabu','source':pin,
        'profile_sha256':hashlib.sha256(profile.read_bytes()).hexdigest(),
        'release':(output/'uboot.release').read_text().strip(),
        'device_tree':p['device_tree'],'compiler_container_image':os.environ['NABU_UBOOT_IMAGE_ID'],
        'hardware_tested':False,'flashed':False}
(output/'compile-receipt.json').write_text(json.dumps(record,indent=2)+'\n')
PY
cd "$output"
sha256sum u-boot u-boot-nodtb.bin u-boot.dtb u-boot.gz config uboot.release \
  compile-receipt.json compiler.txt build-packages.txt elf-header.txt > COMPILED_SHA256SUMS
echo 'U-Boot compiled and checked; Android image packaging follows. Nothing booted or flashed.'
