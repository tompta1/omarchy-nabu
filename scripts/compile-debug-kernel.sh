#!/bin/bash
set -euo pipefail
if [[ ${_NABU_KERNEL_COMPILER_LOADED:-} != "$0" ]]; then
  export _NABU_KERNEL_COMPILER_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_KERNEL_COMPILER_LOADED
[[ -d /kernelwork && -d /project/kernel ]] || exit 1
build=/kernelwork/build
output=/project/out/kernel
profile=${NABU_KERNEL_PROFILE:-kernel/sources.json}
python3 /project/scripts/prepare-kernel-source.py
pins=$(python3 - <<'PY'
import hashlib, json, os, sys
sys.path.insert(0,'/project/scripts')
from nabu_paths import PROJECT, project_relative
data=json.loads((PROJECT/project_relative(os.environ.get('NABU_KERNEL_PROFILE','kernel/sources.json'))).read_text())
print(PROJECT/project_relative(data.get('source_directory','upstream/linux-nabu')))
print(data['defconfig'])
print(data.get('kernel_version',''))
base = data.get('config_fragment') or data.get('debug_fragment')
if not base:
    print(PROJECT/'kernel/debug.config')
for fragment in filter(None, (base, data.get('config_override'))):
    path=PROJECT/project_relative(fragment['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest() != fragment['sha256']:
        raise SystemExit('Kernel configuration fragment checksum mismatch')
    print(path)
PY
)
readarray -t pin <<< "$pins"
source=${pin[0]}
git config --global --add safe.directory "$source"
mkdir -p "$build" "$output"
export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
export KBUILD_BUILD_USER=nabu KBUILD_BUILD_HOST=omarchy-nabu-builder
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git -C "$source" show -s --format=%ct HEAD)
export KBUILD_BUILD_TIMESTAMP
KBUILD_BUILD_TIMESTAMP=$(date -u -d "@$SOURCE_DATE_EPOCH")
make -C "$source" O="$build" "${pin[1]}"
cp "$build/.config" "$output/base.config"
"$source/scripts/kconfig/merge_config.sh" -m -O "$build" "$build/.config" "${pin[@]:3}"
make -C "$source" O="$build" olddefconfig
if [[ -n ${pin[2]} ]]; then
  [[ $(make -s -C "$source" O="$build" kernelversion) == "${pin[2]}" ]] || {
    echo 'Prepared source does not match the requested kernel version' >&2; exit 1;
  }
fi
python3 - "$build/.config" <<'PY'
import sys
from pathlib import Path
config = Path(sys.argv[1]).read_text()
for required in ('CONFIG_DEBUG_INFO_DWARF5=y', 'CONFIG_IKCONFIG_PROC=y',
                 'CONFIG_KALLSYMS_ALL=y', 'CONFIG_PSTORE_RAM=y',
                 'CONFIG_FUNCTION_GRAPH_TRACER=y', 'CONFIG_DYNAMIC_DEBUG=y',
                 'CONFIG_SECURITY_LANDLOCK=y'):
  if required not in config.splitlines():
    raise SystemExit('Required kernel setting did not survive Kconfig: ' + required)
profile = __import__('json').loads(Path('/project', __import__('os').environ.get('NABU_KERNEL_PROFILE', 'kernel/sources.json')).read_text())
if profile.get('debug_fragment'):
  for required in ('CONFIG_GENERIC_IRQ_DEBUGFS=y', 'CONFIG_KPROBES=y',
                   'CONFIG_KPROBE_EVENTS=y', 'CONFIG_FUNCTION_GRAPH_RETVAL=y'):
    if required not in config.splitlines():
      raise SystemExit('Iris diagnostic setting did not survive Kconfig: ' + required)
if profile.get('boot', {}).get('mode') == 'normal':
  if 'CONFIG_BTRFS_FS=y' not in config.splitlines():
    raise SystemExit('Normal kernel must include built-in Btrfs root support')
  for disabled in ('GENERIC_IRQ_DEBUGFS', 'KPROBES', 'KPROBE_EVENTS', 'FUNCTION_GRAPH_RETVAL'):
    if any(line in config.splitlines() for line in ('CONFIG_'+disabled+'=y', 'CONFIG_'+disabled+'=m')):
      raise SystemExit('Temporary diagnostic setting remains enabled: ' + disabled)
if 'config_override' in profile:
  overrides = Path('/project', profile['config_override']['path']).read_text()
  for line in overrides.splitlines():
    if line.startswith('CONFIG_') and line not in config.splitlines():
      raise SystemExit('Selected kernel setting did not survive Kconfig: '+line)
PY
make -C "$source" O="$build" -j"${KERNEL_JOBS:-8}" Image Image.gz modules
if grep -qx CONFIG_EFI_ZBOOT=y "$build/.config"; then
  make -C "$source" O="$build" -j"${KERNEL_JOBS:-8}" vmlinuz.efi
  cp "$build/arch/arm64/boot/vmlinuz.efi" "$output/"
fi
make -C "$source" O="$build" -j"${KERNEL_JOBS:-8}" DTC_FLAGS=-@ dtbs
make -s -C "$source" O="$build" kernelrelease > "$output/kernelrelease"
release=$(cat "$output/kernelrelease")
stage=/kernelwork/stage
rm -rf "$stage"
mkdir -p "$stage"
make -C "$source" O="$build" INSTALL_MOD_PATH="$stage/usr" INSTALL_MOD_STRIP=1 modules_install
# Cross-built header host tools are x86 binaries, so do not install them into
# the ARM tablet root. Keep the full build tree in the named build volume.
rm -f "$stage/usr/lib/modules/$release/build" "$stage/usr/lib/modules/$release/source"
tar -C "$stage" -cf "$output/modules.tar" usr/lib/modules
cp "$build/arch/arm64/boot/Image" "$build/arch/arm64/boot/Image.gz" "$output/"
cp "$build/arch/arm64/boot/dts/qcom/sm8150-xiaomi-nabu.dtb" "$output/"
cp "$build/vmlinux" "$build/System.map" "$build/Module.symvers" "$output/"
cp "$build/.config" "$output/config"
"$source/scripts/diffconfig" "$output/base.config" "$output/config" > "$output/config.diff"
cp "/project/$profile" "$output/sources.json"
"${CROSS_COMPILE}gcc" --version > "$output/toolchain.txt"
"${CROSS_COMPILE}readelf" -h "$output/vmlinux" | tee "$output/elf-header.txt"
"${CROSS_COMPILE}readelf" -S "$output/vmlinux" | grep '[.]debug_info' >/dev/null
cd "$output"
sha256sum Image Image.gz sm8150-xiaomi-nabu.dtb modules.tar vmlinux System.map \
  Module.symvers config sources.json kernel-source-receipt.json toolchain.txt > SHA256SUMS
if grep -qx CONFIG_EFI_ZBOOT=y config; then sha256sum vmlinuz.efi >> SHA256SUMS; fi
echo "Built Nabu kernel: $release"
