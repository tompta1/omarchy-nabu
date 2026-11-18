#!/bin/bash
# Target-specific installer. --probe is read-only; --flash replaces the OS.
set -euo pipefail
if [[ ${_NABU_FLASH_SCRIPT_LOADED:-} != "$0" ]]; then
  export _NABU_FLASH_SCRIPT_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_FLASH_SCRIPT_LOADED
mode=${1:---probe}
[[ $mode == "--probe" || $mode == "--flash" ]] || exit 2
if (( $# )); then shift; fi
fastboot_bin=${NABU_FASTBOOT_BIN:-fastboot}
reboot_timeout=${NABU_FASTBOOT_REBOOT_TIMEOUT:-600}
[[ $reboot_timeout =~ ^[1-9][0-9]{0,3}$ ]] && (( reboot_timeout >= 60 && reboot_timeout <= 3600 )) || {
  echo 'NABU_FASTBOOT_REBOOT_TIMEOUT must be 60..3600 seconds (default 600).' >&2; exit 2;
}
project=$(cd "$(dirname "$0")/.." && pwd)
export NABU_OUTPUT_REL=${NABU_OUTPUT_REL:-out/install}
if (( $# )); then
  [[ $# == 2 && $1 == --output ]] || { echo 'Usage: flash-tablet-install.sh [--probe|--flash] [--output DIR]' >&2; exit 2; }
  NABU_OUTPUT_REL=$(python3 "$project/scripts/nabu_paths.py" relative "$2")
fi
output=$(python3 "$project/scripts/nabu_paths.py" output)
images=$output/images
python3 "$project/scripts/nabu_layout.py" --flash-check
root_bytes=$(python3 "$project/scripts/nabu_layout.py" root_partition_bytes)
boot_bytes=$(python3 "$project/scripts/nabu_layout.py" boot_partition_bytes)
# Refuse stale/mixed images or a different local layout before any write.
if [[ $mode == --flash ]]; then
  python3 "$project/scripts/nabu_images.py" verify --output "$output"
fi
mapfile -t devices < <(timeout -k 2 10 "$fastboot_bin" devices | awk '$2 == "fastboot" { print $1 }')
(( ${#devices[@]} == 1 )) || { echo 'Exactly one fastboot device is required' >&2; exit 1; }
serial=${devices[0]}
getvar() {
  local result
  result=$(timeout -k 2 15 "$fastboot_bin" -s "$serial" getvar "$1" 2>&1)
  printf '%s\n' "$result"
}
product=$(getvar product)
grep -Eq 'product:[[:space:]]*nabu([[:space:]]|$)' <<< "$product" || { echo 'Expected Xiaomi nabu' >&2; exit 1; }
printf '%s\n' "$product"
esp_bytes=$(python3 "$project/scripts/nabu_layout.py" esp_partition_bytes)
for partition in userdata cust rawdump; do
  result=$(getvar "partition-size:$partition")
  printf '%s\n' "$result"
  value=$(sed -nE "s/.*partition-size:$partition:[[:space:]]*(0x[0-9a-fA-F]+).*/\1/p" <<< "$result")
  [[ $value =~ ^0x[0-9a-fA-F]+$ ]] || { echo 'Unable to validate partition size' >&2; exit 1; }
  case $partition in userdata) expected=$root_bytes;; cust) expected=$boot_bytes;; rawdump) expected=$esp_bytes;; esac
  (( value == expected )) || { echo "Partition size differs from recorded tablet: $partition" >&2; exit 1; }
done
[[ $mode == "--flash" ]] || exit 0
cd "$output"
# Recheck after probing so the actual pair is still the one in the receipt.
python3 "$project/scripts/nabu_images.py" verify --output "$output"
(( $(stat -c %s images/rootfs.img) <= root_bytes ))
(( $(stat -c %s images/boot.img) == boot_bytes ))
echo 'Writing cust, then userdata last. Retaining existing U-Boot, ESP and persist.'
timeout -k 5 600 "$fastboot_bin" -s "$serial" -S 256M flash cust "$images/boot.img"
timeout -k 5 2400 "$fastboot_bin" -s "$serial" -S 256M flash userdata "$images/rootfs.img"
echo 'Both flash commands acknowledged. Requesting reboot; device writeback may take several minutes.'
if timeout -k 2 "$reboot_timeout" "$fastboot_bin" -s "$serial" reboot; then
  exit 0
else
  reboot_result=$?
  echo 'Reboot is not confirmed. Keep USB connected and allow writeback to finish.' >&2
  echo 'A timeout does not prove a safe reset: inspect before forcing Power or repeating any flash.' >&2
  exit "$reboot_result"
fi
