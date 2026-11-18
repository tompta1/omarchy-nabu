#!/bin/bash
# FW-03: build only the ARM64 EFI loader; never install it on a device.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
cd "$project"
export NABU_LIMINE_OUTPUT_REL=${NABU_LIMINE_OUTPUT_REL:-}
python3 - <<'PY'
import hashlib,json,os,tarfile
from pathlib import Path
from urllib.request import urlopen
profile=json.loads(Path('profiles/nabu/limine.json').read_text())
name=profile['source_directory']
if '/' in name or not name.startswith('limine-'):
    raise SystemExit('Invalid Limine source directory')
requested=os.environ['NABU_LIMINE_OUTPUT_REL']
output=Path(requested or Path('out')/name).resolve()
if not output.is_relative_to(Path('out').resolve()) or output == Path('out').resolve():
    raise SystemExit('Limine output must be below out/')
output.mkdir(parents=True,exist_ok=True)
if (output/'BOOTAA64.EFI').exists():
    raise SystemExit('Completed Limine output exists; inspect/reuse it')
archive=output/'source.tar.xz'
if not archive.exists():
    archive.write_bytes(urlopen(profile['source_url'],timeout=90).read())
if hashlib.sha256(archive.read_bytes()).hexdigest()!=profile['source_sha256']:
    raise SystemExit('Limine source checksum mismatch')
source=Path('upstream')/name
if not source.exists():
    with tarfile.open(archive) as tar:
        if any(Path(m.name).parts[0]!=name for m in tar.getmembers()):
            raise SystemExit('Unexpected Limine archive root')
        tar.extractall('upstream',filter='data')
# Reject modified cached sources rather than trusting the directory name.
with tarfile.open(archive) as tar:
    for member in tar.getmembers():
        path=Path('upstream')/member.name
        if member.isfile():
            if path.is_symlink() or path.read_bytes()!=tar.extractfile(member).read():
                raise SystemExit('Cached Limine source differs from pinned archive')
        elif member.issym():
            if not path.is_symlink() or str(path.readlink())!=member.linkname:
                raise SystemExit('Cached Limine source symlink changed')
(output/'profile.json').write_text(json.dumps(profile,indent=2)+'\n')
PY
version=$(python3 -c 'import json;print(json.load(open("profiles/nabu/limine.json"))["version"])')
output=${NABU_LIMINE_OUTPUT_REL:-out/limine-$version}
output=$(python3 scripts/nabu_paths.py relative "$output")
"$project/scripts/host-podman.sh" run --rm --name omarchy-nabu-limine-build \
  --platform linux/amd64 -v "$project:/project:z" \
  -e LIMINE_VERSION="$version" -e "LIMINE_OUTPUT=$output" -e "LIMINE_JOBS=${LIMINE_JOBS:-4}" \
  localhost/omarchy-nabu-kernel-builder:ubuntu2404 -c '
set -euo pipefail
src=/project/upstream/limine-$LIMINE_VERSION
dst=/project/$LIMINE_OUTPUT
mkdir -p "$dst/build"
cd "$dst/build"
TOOLCHAIN_FOR_TARGET=aarch64-linux-gnu- "$src/configure" --enable-uefi-aarch64
make -j"$LIMINE_JOBS"
cp bin/BOOTAA64.EFI "$dst/BOOTAA64.EFI"
# The build-machine CLI is x86_64. Ship an ARM64 utility for native maintenance,
# from the same generated upstream source and release as the EFI application.
aarch64-linux-gnu-gcc -O2 -static -std=c99 -D_FILE_OFFSET_BITS=64 \
  -DLIMINE_NO_BIOS -DLIMINE_DATADIR=\"/usr/share/limine\" \
  bin/limine.c -o "$dst/limine-aarch64"
cp "$src/COPYING" "$src/3RDPARTY.md" "$dst/"
aarch64-linux-gnu-objdump -f "$dst/BOOTAA64.EFI" > "$dst/pe-header.txt"
cd "$dst"
sha256sum BOOTAA64.EFI limine-aarch64 source.tar.xz profile.json COPYING 3RDPARTY.md > SHA256SUMS
' > "$output/build.log" 2>&1
