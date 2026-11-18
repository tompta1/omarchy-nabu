#!/bin/bash
# Package compiled firmware with the audited Fedora Android tools, without USB.
set -euo pipefail
if [[ ${_NABU_UBOOT_PACKAGER_LOADED:-} != "$0" ]]; then
  export _NABU_UBOOT_PACKAGER_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_UBOOT_PACKAGER_LOADED
cd /project
output=$(python3 - <<'PY'
import os,sys
sys.path.insert(0,'scripts')
from nabu_paths import PROJECT,project_relative
p=project_relative(os.environ['NABU_UBOOT_OUTPUT'])
assert len(p.parts)>1 and p.parts[0]=='out'
print(PROJECT/p)
PY
)
(cd "$output" && sha256sum -c COMPILED_SHA256SUMS)
mkbootimg --header_version 0 --kernel "$output/u-boot.gz" --base 0 --pagesize 4096 \
  --output "$output/u-boot-xiaomi-nabu.img"
python3 scripts/nabu_bootimg.py --image "$output/u-boot-xiaomi-nabu.img" \
  --binary "$output/u-boot-nodtb.bin" --dtb "$output/u-boot.dtb" > "$output/bootimg-check.json"
rpm -q android-tools > "$output/packaging-tools.txt"
python3 - "$output" <<'PY'
import json,os,sys
from pathlib import Path
output=Path(sys.argv[1])
record=json.loads((output/'compile-receipt.json').read_text())
record['packaging_container_image']=os.environ['NABU_UBOOT_PACKAGING_IMAGE_ID']
record['packaging_tools']=(output/'packaging-tools.txt').read_text().strip()
record['android_wrapper']=json.loads((output/'bootimg-check.json').read_text())
(output/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
PY
cd "$output"
sha256sum u-boot u-boot-nodtb.bin u-boot.dtb u-boot.gz u-boot-xiaomi-nabu.img \
  config uboot.release compile-receipt.json COMPILED_SHA256SUMS receipt.json \
  bootimg-check.json compiler.txt build-packages.txt elf-header.txt packaging-tools.txt > SHA256SUMS
echo 'U-Boot candidate packaged and verified. Nothing booted or flashed.'
