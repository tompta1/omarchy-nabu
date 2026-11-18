#!/bin/bash
set -euo pipefail
if [[ ${_NABU_BUILD_SCRIPT_LOADED:-} != "$0" ]]; then
  export _NABU_BUILD_SCRIPT_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_BUILD_SCRIPT_LOADED
[[ -f /.omarchy-nabu-build-root ]] || exit 1
work=$(mktemp -d /tmp/nabu-keyboard.XXXXXX)
trap 'rm -rf "$work"' EXIT
mapfile -t inputs < <(python3 - <<'PY'
import json
data = json.load(open('/build/profiles/nabu/keyboard.json'))
print(data['sha256'])
print(*data['urls'], sep='\n')
PY
)
for url in "${inputs[@]:1}"; do
  if curl -fL --connect-timeout 30 --max-time 180 "$url" -o "$work/source.tar.gz"; then break; fi
done
printf '%s  %s\n' "${inputs[0]}" "$work/source.tar.gz" | sha256sum -c -
mkdir "$work/source"
tar -xf "$work/source.tar.gz" --strip-components=1 -C "$work/source"
make -C "$work/source" -j2 wvkbd-mobintl
install -Dm755 "$work/source/wvkbd-mobintl" /usr/local/bin/wvkbd-mobintl
output=$(python3 /build/scripts/nabu_paths.py output)
cp /build/profiles/nabu/keyboard.json "$output/keyboard-source.json"
# These exact upstream notices cover the GPL program and its included Weston code.
for notice in LICENSE COPYING COPYING_WESTON; do
  install -Dm644 "$work/source/$notice" "/usr/local/share/licenses/wvkbd/$notice"
done
