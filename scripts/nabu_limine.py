"""FW-03: assemble an offline ARM64 Limine → retained GRUB test bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
from nabu_paths import PROJECT


def verify_arm64_efi(path):
    data = Path(path).read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('Not an EFI executable')
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    if offset+24 > len(data) or data[offset:offset+4] != b'PE\0\0':
        raise ValueError('Invalid PE header')
    if struct.unpack_from('<H', data, offset+4)[0] != 0xaa64:
        raise ValueError('Loader must be ARM64')
    # EFI applications use the PE32+ optional header and subsystem 10.
    if offset+94 > len(data) or struct.unpack_from('<H', data, offset+24)[0] != 0x20b:
        raise ValueError('Expected PE32+ EFI application')
    if struct.unpack_from('<H', data, offset+92)[0] != 10:
        raise ValueError('Expected EFI application subsystem')


def test_config(direct=False):
    # Paths stay on the FAT volume containing this candidate, independent of
    # UEFI variable support. GRUB retains its embedded ext4-boot discovery.
    candidate = ('/Omarchy Nabu - kernel candidate\n'
                 '    protocol: efi\n'
                 '    path: boot():/EFI/omarchy-nabu/limine/kernel-candidate.efi\n\n') if direct else ''
    return ('timeout: 10\n'
            'default_entry: 1\n\n'+candidate+
            '/Omarchy Nabu - retained GRUB\n'
            '    protocol: efi\n'
            '    path: boot():/EFI/omarchy-nabu/limine/grub-recovery.efi\n')


def assemble(loader, grub, output, uki=None):
    loader, grub, output = map(Path, (loader, grub, output))
    if loader.resolve() == grub.resolve():
        raise ValueError('Recovery must be a separate retained GRUB loader')
    for path in (loader, grub):
        verify_arm64_efi(path)
    if uki is not None:
        verify_arm64_efi(uki)
    if output.exists():
        raise ValueError('Output already exists; inspect/reuse it')
    directory = output/'EFI/omarchy-nabu/limine'
    directory.mkdir(parents=True)
    shutil.copyfile(loader, directory/'BOOTAA64.EFI')
    shutil.copyfile(grub, directory/'grub-recovery.efi')
    if uki is not None:
        shutil.copyfile(uki, directory/'kernel-candidate.efi')
    (directory/'limine.conf').write_text(test_config(direct=uki is not None))
    manifest = {'purpose': 'Limine ARM64 direct UKI test' if uki is not None else 'Limine ARM64 EFI handoff test via retained GRUB',
                'hardware_validated': False, 'changes_firmware_default': False,
                'entry': '/EFI/omarchy-nabu/limine/BOOTAA64.EFI',
                'files': {str(p.relative_to(output)): {
                    'bytes': p.stat().st_size,
                    'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in sorted(directory.iterdir())}}
    manifest['required_bytes'] = sum(p['bytes'] for p in manifest['files'].values())
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loader', type=Path, required=True)
    parser.add_argument('--grub', type=Path, required=True)
    parser.add_argument('--uki', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((PROJECT/'out').resolve()) or output == (PROJECT/'out').resolve():
        parser.error('Output must be below ignored out/')
    try:
        result = assemble(args.loader, args.grub, output, args.uki)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
    print('Offline bundle prepared; bytes required:', result['required_bytes'])


def boot_budget(capacity, current_uki, recovery, other_files, snapshot_limit=6,
                usage_percent=85):
    """Budget distinct snapshot kernels plus one in-progress update UKI.

    Snapshot tooling may deduplicate shared kernels. That saving cannot be
    assumed when promising recovery across successive kernel updates.
    """
    values = (capacity, current_uki, recovery, other_files, snapshot_limit, usage_percent)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError('Boot budget requires nonnegative integer sizes/counts')
    if not capacity or not current_uki or not 1 <= usage_percent <= 99:
        raise ValueError('Invalid boot capacity, UKI size or usage limit')
    required = current_uki * (snapshot_limit+2) + recovery + other_files
    limit = capacity * usage_percent // 100
    return {'capacity_bytes': capacity, 'usage_limit_percent': usage_percent,
            'snapshot_slots': snapshot_limit, 'update_reserve_bytes': current_uki,
            'required_bytes': required, 'limit_bytes': limit, 'fits': required <= limit}


def default_config(cmdline=None, *, slot=None):
    """Single tested-kernel entry; firmware remains provided by U-Boot."""
    if cmdline is not None and (not cmdline or any(c in cmdline for c in '\r\n\0')):
        raise ValueError('Expected a single nonempty kernel command line')
    if slot is not None:
        if slot not in ('local', 'debug', 'debug-next', 'debug-pm') or cmdline is None:
            raise ValueError('Expected a selected Nabu kernel and its command line')
        # Upstream matches this child by kernel name and updates it in place.
        # A numeric default of 1 would select the folder and disable autoboot.
        kernel = 'linux-nabu-'+slot
        menu = ('timeout: 3\n'
                f'default_entry: Omarchy Nabu/{kernel}\n\n'
                f'/+Omarchy Nabu\n  //{kernel}\n')
    else:
        menu = 'timeout: 3\ndefault_entry: 1\n\n/Omarchy Nabu\n'
    return (menu +
            '    protocol: efi\n'
            '    path: boot():/EFI/Linux/omarchy.efi\n'
            + ('    cmdline: '+cmdline+'\n' if cmdline is not None else ''))


def assemble_default(loader, uki, output):
    """Create a source-recipe ESP tree, with one loader version and one UKI.

    The Fedora path supports the board's existing firmware entry; EFI/BOOT is
    the standard fallback. Both contain identical Limine bytes. No GRUB input.
    """
    loader, uki, output = map(Path, (loader, uki, output))
    for path in (loader, uki):
        verify_arm64_efi(path)
    if output.exists():
        raise ValueError('Default ESP tree already exists')
    output.mkdir(parents=True)
    for directory, name in (('EFI/BOOT', 'BOOTAA64.EFI'),
                            ('EFI/fedora', 'shimaa64.efi')):
        target = output/directory
        target.mkdir(parents=True)
        shutil.copyfile(loader, target/name)
        (target/'limine.conf').write_text(default_config())
    target = output/'EFI/Linux/omarchy.efi'
    target.parent.mkdir(parents=True)
    shutil.copyfile(uki, target)
    files = {str(p.relative_to(output)): {
        'bytes': p.stat().st_size,
        'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(output.rglob('*')) if p.is_file()}
    record = {'schema': 1, 'boot_chain': 'U-Boot -> Limine -> UKI',
              'kernel_entries': 1, 'loader_versions': 1, 'files': files,
              'required_bytes': sum(item['bytes'] for item in files.values()),
              'hardware_tested': False}
    (output/'manifest.json').write_text(json.dumps(record, indent=2)+'\n')
    return record


def assemble_loader(loader, output):
    """ESP-only Limine files; the authoritative menu lives on FAT /boot.

    Limine scans the boot drive's partitions when its ESP has no configuration.
    An adjacent ESP menu would shadow the snapshot tool's /boot/limine.conf.
    """
    loader, output = Path(loader), Path(output)
    verify_arm64_efi(loader)
    if output.exists():
        raise ValueError('Loader ESP tree already exists')
    output.mkdir(parents=True)
    files = {}
    for name in ('EFI/BOOT/BOOTAA64.EFI', 'EFI/fedora/shimaa64.efi'):
        target = output/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(loader, target)
        files[name] = {'bytes': target.stat().st_size,
                       'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    record = {'schema': 1, 'boot_chain': 'U-Boot -> Limine -> UKI',
              'loader_versions': 1, 'configuration_volume': 'boot',
              'files': files, 'required_bytes': sum(item['bytes'] for item in files.values()),
              'hardware_tested': False}
    (output/'manifest.json').write_text(json.dumps(record, indent=2)+'\n')
    return record


def prepare_boot(uki, tree, slot='local'):
    """Install the initial single-kernel menu on an isolated FAT boot staging tree.

    The full snapshot tooling must subsequently own this same menu. This helper
    prepares initial files only; it does not claim snapshot/update integration.
    """
    uki, tree = Path(uki), Path(tree)
    verify_arm64_efi(uki)
    # Snapshot sync rewrites the menu cmdline, not the embedded UKI section.
    # Validate the source UKI first, preserving every reviewed board argument.
    from nabu_image_boot import boot_checks
    sections = boot_checks.uki.read_sections(uki, filesystem='btrfs')
    cmdline = sections[b'.cmdline'].rstrip(b'\0\n').decode('ascii')
    if not tree.is_dir() or tree.is_symlink():
        raise ValueError('An isolated boot staging directory is required')
    text = default_config(cmdline, slot=slot)
    config = tree/'limine.conf'
    target = tree/'EFI/Linux/omarchy.efi'
    # Refuse parent symlinks as well as existing payloads before modifying files.
    if (config.exists() or config.is_symlink() or target.exists() or target.is_symlink()
            or any(p.is_symlink() for p in (tree/'EFI', tree/'EFI/Linux'))):
        raise ValueError('Boot menu/payload already exists or has unsafe parents')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(uki, target)
    config.write_text(text)


def install_snapshot_initramfs(root):
    """Select the upstream systemd snapshot hook in a marked build root only."""
    root = Path(root)
    if not (root/'.omarchy-nabu-build-root').is_file():
        raise ValueError('Snapshot configuration requires a marked isolated build root')
    required = ('usr/lib/initcpio/install/sd-btrfs-overlayfs',
                'usr/lib/limine/overlayfs-setup',
                'usr/lib/systemd/system/overlayfs-setup.service',
                'usr/bin/limine-snapper-sync', 'usr/bin/limine-snapper-restore',
                'usr/bin/limine-update', 'usr/bin/snapper')
    for name in required:
        if not (root/name).is_file():
            raise ValueError('Missing snapshot recovery component: '+name)
    target = root/'etc/mkinitcpio.conf.d/zz-nabu.conf'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((PROJECT/'profiles/nabu/mkinitcpio-limine.conf').read_bytes())


def verify_snapshot_listing(listing, release, config):
    """Check the actual initramfs inventory, not only its build settings."""
    import re
    if not re.fullmatch(r'[A-Za-z0-9._+-]+', release):
        raise ValueError('Invalid snapshot kernel release')
    names = {line.removeprefix('./').rstrip('/') for line in listing.splitlines()}
    required = {'usr/lib/limine/overlayfs-setup',
                'usr/lib/systemd/system/overlayfs-setup.service',
                'usr/lib/systemd/system/initrd.target.wants/overlayfs-setup.service',
                'usr/bin/bash', 'usr/bin/btrfs', 'usr/bin/findmnt', 'usr/bin/mount',
                'usr/lib/firmware/qcom/a630_sqe.fw', 'usr/lib/firmware/qcom/a640_gmu.bin',
                'usr/lib/firmware/qcom/sm8150/xiaomi/nabu/a640_zap.mbn'}
    missing = required-names
    if missing:
        raise ValueError('Nabu initramfs is missing: '+', '.join(sorted(missing)))
    settings = set(config.splitlines())
    for symbol, directory, module in (('BTRFS_FS', 'btrfs', 'btrfs'),
                                      ('OVERLAY_FS', 'overlayfs', 'overlay')):
        if 'CONFIG_'+symbol+'=y' in settings:
            continue
        stem = 'usr/lib/modules/'+release+'/kernel/fs/'+directory+'/'+module+'.ko'
        if ('CONFIG_'+symbol+'=m' not in settings or
                not any(stem+suffix in names for suffix in ('', '.gz', '.xz', '.zst'))):
            raise ValueError('Snapshot initramfs lacks matching '+symbol+' support')


def verify_snapshot_initramfs(uki, config):
    """Use the build root's mkinitcpio reader on a validated Btrfs UKI."""
    import subprocess
    import tempfile
    from nabu_image_boot import boot_checks
    sections = boot_checks.uki.read_sections(uki, filesystem='btrfs')
    release = sections[b'.uname'].rstrip(b'\0\n').decode('ascii')
    with tempfile.TemporaryDirectory(prefix='nabu-snapshot-initrd-') as directory:
        initrd = Path(directory)/'initrd'
        initrd.write_bytes(sections[b'.initrd'])
        listing = subprocess.check_output(['lsinitcpio', '--list', '--nocolor', str(initrd)], text=True)
    verify_snapshot_listing(listing, release, Path(config).read_text())
    return release


def stage_image_boot(source, output, slot):
    """Stage only the selected kernel's boot files, preserving the build root."""
    import re
    from nabu_image_boot import boot_checks
    source, output = Path(source), Path(output)
    if slot not in ('local', 'debug', 'debug-next', 'debug-pm') or output.exists():
        raise ValueError('Use a selected Nabu slot and a new boot staging tree')
    uki = source/'boot/EFI/arch'/('arch-linux-nabu-'+slot+'.efi')
    sections = boot_checks.uki.read_sections(uki, filesystem='btrfs')
    release = sections[b'.uname'].rstrip(b'\0\n').decode('ascii')
    if not re.fullmatch(r'[A-Za-z0-9._+-]+', release):
        raise ValueError('Unsafe kernel release')
    modules = source/'usr/lib/modules'/release
    payloads = ((modules/'vmlinuz', 'vmlinux-'+release, b'.linux'),
                (modules/'nabu.dtb', 'dtb-'+release, b'.dtb'))
    for path, _, section in payloads:
        if path.read_bytes() != sections[section]:
            raise ValueError('Staged kernel/DTB differs from selected UKI')
    output.mkdir(parents=True)
    prepare_boot(uki, output, slot)
    for path, name, _ in payloads:
        shutil.copyfile(path, output/name)
    return release


def runtime_config(slot, cmdline):
    """Board override for the upstream entry/Snapper tools, with no machine ID."""
    if slot not in ('local', 'debug', 'debug-next', 'debug-pm'):
        raise ValueError('Unsupported Nabu kernel slot')
    args = cmdline.split()
    # These are build-generated arguments, not shell input or menu fragments.
    import re
    if (not args or any(not re.fullmatch(r'[A-Za-z0-9_+=:/@.,-]+', arg) for arg in args)
            or [arg for arg in args if arg.startswith('root=')] != ['root=PARTLABEL=userdata']
            or [arg for arg in args if arg.startswith('rootfstype=')] != ['rootfstype=btrfs']
            or [arg for arg in args if arg.startswith('rootflags=')] != ['rootflags=subvol=@']
            or any(arg.startswith('earlycon=efifb') or arg == 'keep_bootcon' for arg in args)):
        raise ValueError('Invalid Nabu Btrfs runtime command line')
    return ('# OS-11: upstream Limine/Snapper tooling on the Nabu FAT boot volume.\n'
            'ESP_PATH="/boot"\nTARGET_OS_NAME="Omarchy Nabu"\n'
            'ENABLE_UKI=yes\nCUSTOM_UKI_NAME="omarchy"\n'
            'MKINITCPIO_FALLBACK=no\nENABLE_LIMINE_FALLBACK=no\n'
            'FIND_BOOTLOADERS=no\nSKIP_UEFI=yes\n'
            f'BOOT_ORDER="linux-nabu-{slot}, Snapshots"\n'
            'KERNEL_CMDLINE[default]="'+' '.join(args)+'"\n'
            f'MKINITCPIO_UKI_OPTIONS="--ukiconfig /etc/kernel/uki-nabu-{slot}.conf"\n'
            'SNAPPER_CONFIG_NAME="root"\nRESTORE_METHOD=replace\n'
            'SNAPSHOT_WRITABLE=no\nMAX_SNAPSHOT_ENTRIES=6\n'
            'LIMIT_USAGE_PERCENT=85\nSNAPSHOT_FORMAT_CHOICE=5\n')
