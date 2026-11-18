#!/bin/bash
set -euo pipefail
input=$(python3 /project/scripts/nabu_paths.py kernel)
kernel=$input/vmlinux
address=$(aarch64-linux-gnu-nm "$kernel" | awk '$3 == "start_kernel" { print $1 }')
[[ -n $address ]]
aarch64-linux-gnu-addr2line -f -e "$kernel" "$address" > "$input/symbol-smoke.txt"
grep '/init/main.c:' "$input/symbol-smoke.txt"
