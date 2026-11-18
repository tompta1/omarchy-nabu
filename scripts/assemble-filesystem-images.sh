#!/bin/bash
# Native filesystem assembly from an explicitly prepared ARM root; no mounts/devices.
set -euo pipefail
root=/work/root
project=/project
[[ $EUID == 0 && -f /work/omarchy-nabu-image-prepared ]] || {
  echo 'Prepare the image root with assemble-tablet-install.sh first.' >&2; exit 1;
}
python3 - <<'PY_VERSION'
import re,subprocess
version=subprocess.check_output(['btrfs','version'],text=True)
match=re.search(r'v([0-9]+)[.]([0-9]+)',version)
if not match or tuple(map(int,match.groups())) < (7,1):
    raise SystemExit('Filesystem assembly requires btrfs-progs >=7.1')
PY_VERSION
output=$(python3 "$project/scripts/nabu_paths.py" output)
images=$output/images
[[ ! -e $images ]] || { echo 'Choose a new output; images already exist.' >&2; exit 1; }
manager=${NABU_BOOT_MANAGER:-grub}
[[ $manager == grub || $manager == limine ]] || exit 2
stage=
boot_stage=
finish() {
  if [[ -n $stage ]]; then rm -rf "$stage"; fi
  if [[ -n $boot_stage ]]; then rm -rf "$boot_stage"; fi
}
trap finish EXIT
mkdir -p "$output"
{ btrfs version; mkfs.fat --help 2>&1 | head -1 || true; uname -m; } > "$output/filesystem-tools.txt"
mkdir -p "$images"
boot_uuid=$(python3 "$project/scripts/nabu_layout.py" boot_filesystem_uuid)
root_uuid=$(python3 "$project/scripts/nabu_layout.py" root_filesystem_uuid)
boot_bytes=$(python3 "$project/scripts/nabu_layout.py" boot_partition_bytes)
root_bytes=$(python3 "$project/scripts/nabu_layout.py" root_partition_bytes)
if [[ $manager == limine ]]; then
  boot_stage=$(mktemp -d /work/omarchy-nabu-boot-stage.XXXXXX)
  python3 - "$root" "$boot_stage/tree" "$images/boot.img" <<'PY_BOOT'
from pathlib import Path
import sys
sys.path.insert(0,'/project/scripts')
from nabu_limine import stage_image_boot
from nabu_filesystems import build_boot_fat
from nabu_layout import load_layout
from nabu_kernel import select
layout=load_layout()
if layout.get('boot_filesystem_type') != 'vfat':
    raise SystemExit('Limine image requires an explicit FAT boot layout')
stage_image_boot(Path(sys.argv[1]),Path(sys.argv[2]),select()['slot'])
build_boot_fat(Path(sys.argv[2]),Path(sys.argv[3]),layout['boot_filesystem_uuid'],layout['boot_partition_bytes'])
PY_BOOT
else
  truncate -s "$boot_bytes" "$images/boot.img"
  mkfs.ext4 -F -L boot -U "$boot_uuid" -d "$root/boot" "$images/boot.img"
fi
# A separate copy carries the underlying root's GRUB redirect. Reflink where
# possible; never hard-link editable files or remove the source boot tree.
stage=$(mktemp -d /work/omarchy-nabu-image-root.XXXXXX)
mkdir "$stage/@"
cp -a --reflink=auto "$root/." "$stage/@/"
rm -rf "$stage/@/boot"
mkdir -p "$stage/@/boot"
if [[ $manager == grub ]]; then
mkdir -p "$stage/@/boot/grub2" "$stage/@/boot/efi"
cat > "$stage/@/boot/grub2/grub.cfg" <<EOF
search --no-floppy --fs-uuid --set=omarchy_boot $boot_uuid
configfile (\$omarchy_boot)/grub2/grub.cfg
EOF
else
  mkdir -p "$stage/@/efi"
fi
python3 "$project/scripts/nabu_filesystems.py" --stage "$stage" \
  --image "$images/rootfs.img" --uuid "$root_uuid" --limit "$root_bytes"
python3 "$project/scripts/verify-images.py" --images "$images" --boot "$images/boot.img" --compiled --boot-manager "$manager"
echo 'Native filesystem and image checks passed; hardware untested.'
