#!/usr/bin/env python3
"""Manual Actions image builds; test inputs and build scratch stay off GitHub."""
import base64
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def test_layout():
    sector_bytes = 4096
    root = 32 * 1024**3
    boot = 4 * 1024**3
    first = 256
    fat_ids = [uuid.uuid4().hex[:8].upper() for _ in range(2)]
    return {
        'layout_profile': 'tail-boot-v1', 'layout_status': 'proposed',
        'root_partition': 'userdata', 'root_partition_bytes': root,
        'root_start_lba': first, 'boot_start_lba': first + root//sector_bytes,
        'original_root_partition_bytes': root + boot,
        'root_filesystem_uuid': str(uuid.uuid4()),
        'boot_partition': 'nabu_boot', 'boot_partition_bytes': boot,
        'boot_filesystem_type': 'vfat', 'boot_filesystem_uuid': fat_ids[0][:4]+'-'+fat_ids[0][4:],
        'esp_partition': 'rawdump', 'esp_partition_bytes': 128 * 1024**2,
        'esp_filesystem_uuid': fat_ids[1][:4]+'-'+fat_ids[1][4:],
    }


def remove_work_volumes(podman, volumes, log):
    removed = True
    for volume in volumes:
        query = subprocess.run(podman+['ps','-a','--filter','volume='+volume,'--format','{{.ID}}'],
                               stdout=subprocess.PIPE,stderr=log,text=True)
        if query.returncode:
            print(f'Could not list containers for {volume}', file=log)
            removed = False
            continue
        for container in query.stdout.splitlines():
            result = subprocess.run(podman+['rm','--force',container],stdout=log,stderr=subprocess.STDOUT)
            removed &= result.returncode == 0
        exists = subprocess.run(podman+['volume','exists',volume],stdout=log,stderr=subprocess.STDOUT)
        if exists.returncode == 0:
            result = subprocess.run(podman+['volume','rm',volume],stdout=log,stderr=subprocess.STDOUT)
            removed &= result.returncode == 0
        elif exists.returncode != 1:
            print(f'Could not verify whether {volume} exists', file=log)
            removed = False
    return removed


def main():
    os.umask(0o077)
    suffix = os.environ.get('GITHUB_RUN_ID', '')+'-'+os.environ.get('GITHUB_RUN_ATTEMPT', '')
    if not re.fullmatch(r'[0-9]+-[0-9]+', suffix):
        raise ValueError('Run this entrypoint through the manual Actions workflow')
    mode = os.environ.get('CI_TARGET', 'test')
    profile = os.environ.get('CI_PROFILES', 'vanilla')
    if mode not in ('test', 'device') or profile not in ('vanilla', 'tablet-options', 'both'):
        raise ValueError('Unsupported build selection')
    upload = os.environ.get('CI_UPLOAD', 'false') == 'true'
    if upload and (mode != 'device' or os.environ.get('CI_REPOSITORY_PRIVATE') != 'true'):
        raise ValueError('Artifact upload requires a private repository and device mode')
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ValueError('The image runner must be x86_64 Linux')
    if shutil.disk_usage(ROOT).free < 120*1024**3:
        raise ValueError('The image runner needs at least 120 GiB free')
    requested = int(os.environ.get('CI_KERNEL_JOBS', '0'))
    if requested < 0:
        raise ValueError('Kernel job count cannot be negative')
    jobs = requested or min(24, max(1, (os.cpu_count() or 2)-2))
    base = ROOT/'out'/('ci-'+suffix)
    inputs = ROOT/'local'/('ci-'+suffix)
    temporary = Path(os.environ['RUNNER_TEMP'])
    # Actions empties RUNNER_TEMP at job completion, including on failure.
    # Keep diagnostic logs in ignored local storage, never artifact uploads.
    logs = ROOT/'local'/'ci-build-logs'/suffix
    results = temporary/'nabu-image-results'/suffix
    for path in (base, inputs, logs):
        path.mkdir(parents=True, exist_ok=False)
    podman = [str(ROOT/'scripts/host-podman.sh')]
    kernel_volume = 'omarchy-nabu-ci-kernel-'+suffix
    flavours = ['vanilla', 'tablet-options'] if profile == 'both' else [profile]
    volumes = [kernel_volume]+['omarchy-nabu-ci-image-'+name+'-'+suffix for name in flavours]
    env = {key:value for key,value in os.environ.items() if not key.startswith('NABU_')}
    loader = str((base/'limine').relative_to(ROOT))
    variables = inputs/'ubootefi.var'
    env.update(NABU_LAYOUT=str((inputs/'layout.json').relative_to(ROOT)),
               NABU_PROVISION=str((inputs/'provision.json').relative_to(ROOT)),
               NABU_KERNEL_PROFILE='kernel/profiles/7.4-rc3-candidate.json',
               NABU_KERNEL_OUTPUT=str((base/'kernel').relative_to(ROOT)),
               NABU_KERNEL_INPUT_REL=str((base/'kernel').relative_to(ROOT)),
               NABU_BOOT_MANAGER='limine', NABU_LIMINE_OUTPUT_REL=loader,
               NABU_LIMINE_INPUT_REL=loader,
               NABU_KERNEL_WORK_VOLUME=kernel_volume, KERNEL_JOBS=str(jobs))
    status = {'target':mode, 'profiles':flavours, 'verified':False,
              'hardware_tested':False, 'uploaded':False}

    def run(label, command, selected_env=env):
        print(label, flush=True)
        with (logs/(label+'.log')).open('w') as stream:
            result = subprocess.run(command, cwd=ROOT, env=selected_env,
                                    stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(label+' failed; full logs remain private on the runner')

    try:
        if mode == 'test':
            layout = test_layout()
            password = subprocess.check_output(['openssl','passwd','-6','-stdin'],
                         input=secrets.token_urlsafe(40)+'\n', text=True).strip()
            keys = ''
            firmware_vars = b'non-device fixture for offline ESP assembly\n'
        else:
            try:
                layout = json.loads(os.environ['NABU_CI_LAYOUT_JSON'])
                password = os.environ['NABU_CI_PASSWORD_HASH'].strip()
                keys = os.environ.get('NABU_CI_AUTHORIZED_KEYS', '')
                firmware_vars = base64.b64decode(os.environ['NABU_CI_UBOOT_VARS_B64'].strip(), validate=True)
            except (KeyError, ValueError):
                raise ValueError('Supply the device layout, login hash and base64 U-Boot EFI variables') from None
        if not isinstance(layout, dict) or layout.get('layout_profile') != 'tail-boot-v1' or not firmware_vars:
            raise ValueError('Limine image build requires a tail-boot layout and EFI variables')
        (inputs/'layout.json').write_text(json.dumps(layout)+'\n')
        variables.write_bytes(firmware_vars)
        (inputs/'password.hash').write_text(password+'\n')
        (inputs/'authorized-keys.pub').write_text(keys)
        provision = {'username':os.environ.get('CI_USERNAME', 'tablet'),
                     'autologin':os.environ.get('CI_AUTOLOGIN', 'true') == 'true',
                     'password_hash_file':str((inputs/'password.hash').relative_to(ROOT)),
                     'authorized_keys_file':str((inputs/'authorized-keys.pub').relative_to(ROOT))}
        (inputs/'provision.json').write_text(json.dumps(provision)+'\n')
        run('validate-layout', [sys.executable,'scripts/nabu_layout.py'])
        run('validate-provisioning', [sys.executable,'scripts/nabu_provision.py'])
        run('check-arm64-binfmt', podman+['run','--rm','--platform','linux/arm64',
                                        'docker.io/library/alpine:3.22','uname','-m'])
        if (logs/'check-arm64-binfmt.log').read_text().splitlines()[-1] != 'aarch64':
            raise RuntimeError('ARM64 binfmt is not working on the runner')
        run('build-kernel', ['bash','scripts/build-debug-kernel.sh'])
        run('build-limine', ['bash','scripts/build-limine.sh'])
        source_run = ''
        for name in flavours:
            output = base/name
            chosen = dict(env, NABU_OUTPUT_REL=str(output.relative_to(ROOT)),
                          NABU_SOURCE_RUN=source_run,
                          NABU_WORK_VOLUME='omarchy-nabu-ci-image-'+name+'-'+suffix,
                          NABU_IMAGE_OPTIONS='profiles/nabu/image-options.'+
                          ('default.json' if name == 'vanilla' else 'example.json'))
            run('build-'+name, ['bash','scripts/build-tablet-image.sh'], chosen)
            run('esp-'+name, podman+['run','--rm','--platform','linux/amd64',
                '-v',f'{ROOT}:/project:z','-e','NABU_LAYOUT='+env['NABU_LAYOUT'],
                'localhost/omarchy-nabu-filesystem-tools:arch','-ec',
                'python3 scripts/assemble-limine-esp.py "$@"','bash',
                '--loader',loader+'/BOOTAA64.EFI','--loader-only','--firmware-vars',
                str(variables.relative_to(ROOT)), '--output',str((output/'esp-component').relative_to(ROOT))])
            receipt = json.loads((output/'image-receipt.json').read_text())
            esp = json.loads((output/'esp-component/receipt.json').read_text())
            if set(receipt['artifacts']) != {'rootfs.img','boot.img'}:
                raise RuntimeError('Image receipt is incomplete')
            if (esp['layout_sha256'] != receipt['layout_sha256']
                    or not esp.get('preserved_uboot_variables') or esp.get('configuration_volume') != 'boot'):
                raise RuntimeError('Limine ESP does not match the OS layout')
            if not (output/'build-state').read_text().startswith('offline-verified;'):
                raise RuntimeError('Offline image verification did not finish')
            source_run = str(output.relative_to(ROOT))
        status['verified'] = True
        if mode == 'device':
            results.parent.mkdir(parents=True, exist_ok=True)
            base.rename(results)
        print('Kernel and selected OS images passed offline build verification.', flush=True)
    finally:
        with (logs/'cleanup.log').open('w') as stream:
            cleanup_ok = remove_work_volumes(podman, volumes, stream)
        shutil.rmtree(inputs)
        if base.exists():
            shutil.rmtree(base)
        status['scratch_removed'] = cleanup_ok
        (logs/'result.json').write_text(json.dumps(status, indent=2)+'\n')
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a') as stream:
                stream.write('Image build: '+('offline verified' if status['verified'] else 'failed')+'.\n\n')
                stream.write('Test mode discards images; device results stay on the runner unless private upload is selected. '
                             'No flash or hardware qualification is performed.\n')
        if not cleanup_ok:
            raise RuntimeError('Could not confirm cleanup of all run-owned work volumes')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('CI image build stopped: '+str(error), file=sys.stderr)
        raise SystemExit(1)
