#!/usr/bin/env python3
"""Build a separate ARM64 GRUB candidate from a signed package; never install it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile
from nabu_layout import load_layout
from nabu_paths import PROJECT, project_relative


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_package(path, pin):
    if path.stat().st_size != pin['package_bytes'] or digest(path) != pin['package_sha256']:
        raise ValueError('GRUB package differs from the pinned stable artifact')


def verify_signature(package, keyring, fingerprint):
    if keyring.is_file() and keyring.read_bytes().startswith(b'-----BEGIN PGP PUBLIC KEY BLOCK-----'):
        with tempfile.TemporaryDirectory(prefix='nabu-grub-keyring-') as temporary:
            binary = Path(temporary)/'public.gpg'
            converted = subprocess.check_output(['gpg','--batch','--dearmor'],input=keyring.read_bytes())
            binary.write_bytes(converted)
            return verify_signature(package,binary,fingerprint)
    result = subprocess.run(['gpgv', '--status-fd', '1', '--keyring', str(keyring),
                             str(package)+'.sig', str(package)], capture_output=True, text=True, check=True)
    if not any(line.startswith('[GNUPG:] VALIDSIG '+fingerprint+' ')
               for line in result.stdout.splitlines()):
        raise ValueError('GRUB package signature does not match the pinned signer')
    return result.stdout + result.stderr


def embedded_config(boot_uuid, version):
    # Keep the standalone memdisk prefix so modules always match this loader.
    # The external Nabu menu selects the UKI; it does not replace GRUB's modules.
    return (f'search --no-floppy --fs-uuid --set=nabu_boot {boot_uuid}\n'
            'if [ -f ($nabu_boot)/grub2/grub-candidate.env ]; then\n'
            f"  set nabu_grub_candidate_version='{version}'\n"
            '  save_env -f ($nabu_boot)/grub2/grub-candidate.env nabu_grub_candidate_version\n'
            'fi\n'
            'configfile ($nabu_boot)/grub2/grub.cfg\n')


def verify_efi(path):
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('Candidate is not an EFI executable')
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    if (offset+6 > len(data) or data[offset:offset+4] != b'PE\0\0'
            or struct.unpack_from('<H', data, offset+4)[0] != 0xaa64):
        raise ValueError('Candidate is not an ARM64 PE executable')


def rescue_config(boot_uuid):
    import uuid
    boot_uuid = str(uuid.UUID(boot_uuid))
    return (f'search --no-floppy --fs-uuid --set=root {boot_uuid}\n'
            'chainloader /EFI/arch/arch-linux-nabu-stock.efi\nboot\n')


def assemble(package, keyring, sysroot, output):
    pin = json.loads((PROJECT/'profiles/nabu/grub.json').read_text())
    layout = load_layout()
    relative = project_relative(output)
    if len(relative.parts) < 2 or relative.parts[0] != 'out':
        raise ValueError('Candidate output must be below out/')
    output = PROJECT/relative
    if sysroot.resolve() == Path('/') or not os.path.samefile(PROJECT, sysroot/'build'):
        raise ValueError('Use an isolated ARM sysroot with this project bind-mounted at /build')
    validate_package(package, pin)
    signature = verify_signature(package, keyring, pin['signer_fingerprint'])
    output.mkdir(parents=True, exist_ok=False)
    (output/'signature.log').write_text(signature)
    extracted = output/'package'
    extracted.mkdir()
    with tarfile.open(package, 'r:*') as archive:
        archive.extractall(extracted, filter='data')
    tools = extracted/'usr/bin'
    header = (tools/'grub-mkstandalone').read_bytes()[:64]
    if header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != 183:
        raise ValueError('GRUB build tools are not ARM64 ELF executables')

    def inside(path):
        return '/build/'+str(path.relative_to(PROJECT))

    def run(tool, *args, env=None):
        return subprocess.run(['chroot', str(sysroot), inside(tools/tool), *args],
                              capture_output=True, text=True, check=True, env=env)

    version = run('grub-mkstandalone', '--version').stdout.strip()
    if not version.endswith('(GRUB) '+pin['version']):
        raise ValueError('GRUB build tool version differs from the pinned package')
    config = output/'grub.cfg'
    config.write_text(embedded_config(layout['boot_filesystem_uuid'], pin['version']))
    run('grub-script-check', inside(config))
    candidate = output/'grubaa64-candidate.efi'
    # The sysroot can stay read-only; GRUB's scratch files belong to this output.
    with tempfile.TemporaryDirectory(dir=output, prefix='.grub-build-') as scratch:
        build = run('grub-mkstandalone', '--format='+pin['target'],
                    '--directory='+inside(extracted/'usr/lib/grub'/pin['target']),
                    '--locales=', '--fonts=', '--themes=',
                    '--modules='+' '.join(pin['preload_modules']), '--compress=xz',
                    '--output='+inside(candidate), 'boot/grub/grub.cfg='+inside(config),
                    env={**os.environ, 'TMPDIR': inside(Path(scratch))})
    (output/'build.log').write_text(build.stdout+build.stderr)
    verify_efi(candidate)
    run('grub-file', '--is-arm64-efi', inside(candidate))
    rescue = output/'grubaa64-rescue.efi'
    rescue_script = output/'rescue.cfg'
    rescue_script.write_text(rescue_config(layout['boot_filesystem_uuid']))
    run('grub-script-check', inside(rescue_script))
    with tempfile.TemporaryDirectory(dir=output, prefix='.grub-rescue-') as scratch:
        run('grub-mkstandalone', '--format='+pin['target'],
            '--directory='+inside(extracted/'usr/lib/grub'/pin['target']),
            '--locales=', '--fonts=', '--themes=',
            '--modules='+' '.join(pin['preload_modules']), '--compress=xz',
            '--output='+inside(rescue), 'boot/grub/grub.cfg='+inside(rescue_script),
            env={**os.environ,'TMPDIR':inside(Path(scratch))})
    verify_efi(rescue)
    run('grub-file', '--is-arm64-efi', inside(rescue))
    receipt = {'schema': 1, 'version': pin['version'], 'target': pin['target'],
               'package_sha256': digest(package), 'signature_verified': True,
               'signer_fingerprint': pin['signer_fingerprint'], 'recipe_url': pin['recipe_url'],
               'candidate_sha256': digest(candidate), 'candidate_bytes': candidate.stat().st_size,
               'config_sha256': digest(config), 'rescue_sha256': digest(rescue),
               'rescue_config_sha256': digest(rescue_script),
               'hardware_tested': False, 'installed': False}
    (output/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    (output/'SHA256SUMS').write_text(''.join(digest(output/name)+'  '+name+'\n'
        for name in ('grubaa64-candidate.efi', 'grub.cfg', 'grubaa64-rescue.efi', 'rescue.cfg', 'receipt.json')))
    print('Separate ARM64 GRUB candidate built and verified. Nothing installed or selected.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--keyring', type=Path, required=True)
    parser.add_argument('--sysroot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        assemble(args.package, args.keyring, args.sysroot, args.output)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise SystemExit('GRUB candidate refused: '+str(detail))
