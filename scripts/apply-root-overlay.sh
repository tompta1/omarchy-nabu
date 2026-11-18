#!/bin/bash
set -euo pipefail
[[ $# == 2 && -d $1 && -d $2 ]]
tar -C "$1" --mode='u=rwX,go=rX' -cf - . |
  tar -C "$2" --no-overwrite-dir --no-same-owner -xpf -
