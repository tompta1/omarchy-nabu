#!/usr/bin/env python3
"""Compare our prebuilt UEFI with Pocketblue's pinned Aloha reference."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen
import zipfile
import tempfile

project = Path(__file__).resolve().parent.parent
reference = json.loads((project / 'profiles/nabu/bootchain-references.json').read_text())['pocketblue_aloha_reference']
target = project / 'out/inventory/aloha-reference-comparison.json'
target.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryFile() as archive:
  digest = hashlib.sha256()
  size = 0
  with urlopen(reference['url'], timeout=60) as response:
    while block := response.read(1024 * 1024):
      size += len(block)
      if size > 512 * 1024 * 1024:
        raise SystemExit('Reference archive exceeds the 512 MiB download bound')
      digest.update(block)
      archive.write(block)
  if digest.hexdigest() != reference['sha256']:
    result = {**reference, 'observed_archive_bytes': size,
              'observed_archive_sha256': digest.hexdigest(),
              'comparison': 'not performed: archive differs from pinned recipe checksum'}
    target.write_text(json.dumps(result, indent=2) + '\n')
    print(result['comparison'])
    raise SystemExit(1)
  archive.seek(0)
  with zipfile.ZipFile(archive) as bundle:
    image = bundle.read(reference['member'])
ours = (project / 'out/images/alohauefi.img').read_bytes()
result = {**reference, 'reference_image_sha256': hashlib.sha256(image).hexdigest(),
          'our_image_sha256': hashlib.sha256(ours).hexdigest(), 'byte_identical': image == ours}
target.write_text(json.dumps(result, indent=2) + '\n')
print('Current Aloha image matches Pocketblue 2412.74 NOSB reference:', result['byte_identical'])
