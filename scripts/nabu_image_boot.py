#!/usr/bin/env python3
"""Read-only existing-U-Boot image checks, without loop mounts (OS-05)."""
import argparse
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import os
import stat
import subprocess
import tempfile
import uuid

from nabu_grub import menu_config
from nabu_layout import load_layout
from nabu_limine import default_config

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nabu_boot_inspection', ROOT/'scripts/inspect-nabu-boot.py')
boot_checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot_checks)


def safe_path(path):
    if path == '/':
        return path
    if (not re.fullmatch(r'/[A-Za-z0-9._+/-]+', path)
            or '..' in PurePosixPath(path).parts or str(PurePosixPath(path)) != path):
        raise ValueError('Unsafe image inspection path')
    return path


def parse_stats(text, paths):
    """debugfs can return success for missing files: require each inode/type."""
    records = {}
    current = None
    expected = set(paths)
    for line in text.splitlines():
        if line.startswith('debugfs: stat '):
            name = line.removeprefix('debugfs: stat ').strip('"')
            if name not in expected or name in records:
                raise ValueError('Unexpected/duplicate debugfs stat response')
            current = name
            records[name] = []
        elif current is not None:
            records[current].append(line)
    result = {}
    for path in paths:
        record = '\n'.join(records.get(path, []))
        match = re.search(r'^Inode:\s+\d+\s+Type:\s+(\w+)', record, re.M)
        if not match:
            raise ValueError(f'Missing image inode: {path}')
        result[path] = (match[1], record)
    return result


class Ext4Image:
    filesystem = 'ext4'
    def __init__(self, image, temporary):
        self.image = Path(image)
        self.temporary = Path(temporary)
        self.counter = 0

    def scratch(self):
        self.counter += 1
        return self.temporary/str(self.counter)

    def read_file(self, path, allow_empty=False):
        path = safe_path(path)
        output = self.scratch()
        subprocess.run(['debugfs', '-R', f'dump "{path}" "{output}"', str(self.image)],
                       check=True, capture_output=True, text=True)
        if not output.is_file() or (not allow_empty and not output.stat().st_size):
            raise ValueError(f'Missing/empty image file: {path}')
        return output

    def stats(self, paths):
        paths = sorted(set(safe_path(path) for path in paths))
        commands = self.scratch()
        commands.write_text(''.join(f'stat "{path}"\n' for path in paths))
        result = subprocess.run(['debugfs', '-f', str(commands), str(self.image)],
                                check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return parse_stats(result.stdout, paths)

    def filesystem_uuid(self):
        result = subprocess.run(['dumpe2fs', '-h', str(self.image)], check=True,
                                capture_output=True, text=True)
        matches = re.findall(r'^Filesystem UUID:\s+([0-9a-fA-F-]+)\s*$', result.stdout, re.M)
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous ext4 filesystem UUID')
        return uuid.UUID(matches[0])

    def exists(self, path):
        safe_path(path)
        try:
            self.stats([path])
            return True
        except ValueError as error:
            if str(error).startswith('Missing image inode:'):
                return False
            raise

    def listdir(self, path):
        result = subprocess.run(['debugfs','-R','ls -p '+safe_path(path),str(self.image)],
                                capture_output=True,text=True,check=True)
        return [line.split('/')[5] for line in result.stdout.splitlines()
                if line.startswith('/') and len(line.split('/')) > 5
                and line.split('/')[5] not in ('.','..')]

    def link_target(self, path):
        kind, record = self.stats([path])[path]
        targets = re.findall(r'^Fast link dest: "([^"]+)"$', record, re.M)
        if kind != 'symlink' or len(targets) != 1:
            raise ValueError('Expected an image symlink: '+path)
        return targets[0]


class FatImage:
    """Inspect a FAT image using a private mtools extraction, without mounts."""
    filesystem = 'vfat'

    def __init__(self, image, temporary):
        from nabu_filesystems import fat_geometry
        self.image = Path(image)
        with self.image.open('rb') as stream:
            fat_geometry(stream.read(4096))
        self.temporary = Path(temporary)
        self.top = self.temporary/'fat-files'
        self.top.mkdir()
        self.counter = 0
        subprocess.run(['mcopy', '-i', str(self.image), '-s', '::/*', str(self.top)],
                       check=True, capture_output=True, text=True)

    def path(self, path):
        path = safe_path(path)
        target = self.top/path.lstrip('/')
        if not target.resolve().is_relative_to(self.top.resolve()):
            raise ValueError('FAT image path escapes extraction')
        if not target.exists():
            raise ValueError('Missing image inode: '+path)
        return target

    def read_file(self, path, allow_empty=False):
        import shutil
        source = self.path(path)
        if not source.is_file() or (not allow_empty and not source.stat().st_size):
            raise ValueError('Missing/empty image file: '+path)
        self.counter += 1
        output = self.temporary/('fat-read-'+str(self.counter))
        shutil.copyfile(source, output)
        return output

    def stats(self, paths):
        return {path: ('directory' if self.path(path).is_dir() else 'regular', '')
                for path in paths}

    def exists(self, path):
        try:
            self.path(path)
            return True
        except ValueError as error:
            if str(error).startswith('Missing image inode:'):
                return False
            raise

    def listdir(self, path):
        return [p.name for p in self.path(path).iterdir()]

    def link_target(self, path):
        raise ValueError('FAT images cannot carry Unix symlinks: '+path)

    def filesystem_uuid(self):
        value = subprocess.check_output(['blkid', '-p', '-o', 'value', '-s', 'UUID',
                                         str(self.image)], text=True).strip()
        if not re.fullmatch(r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}', value):
            raise ValueError('Invalid FAT filesystem UUID')
        return value.upper()


class BtrfsImage:
    """Restore into scratch for read-only inspection, without host loop devices."""
    filesystem = 'btrfs'

    def __init__(self, image, temporary):
        from nabu_filesystems import policy
        self.image = Path(image)
        self.temporary = Path(temporary)
        self.top = self.temporary/'restored'
        self.top.mkdir()
        self.subvolumes = policy()['subvolumes']
        tree = subprocess.check_output(['btrfs','inspect-internal','dump-tree','-t','root',str(image)],text=True)
        refs = re.findall(r'key \(FS_TREE ROOT_REF ([0-9]+)\)[^\n]*\n\s*root ref key [^\n]* name ([^\n]+)',tree)
        names = {name:int(rootid) for rootid,name in refs}
        defaults = re.findall(r'location key \(([0-9]+) ROOT_ITEM 0\) type DIR\n[^\n]*\n\s*name: default\n',tree)
        if (len(refs) != len(self.subvolumes) or set(names) != set(self.subvolumes)
                or defaults != [str(names['@'])]):
            raise ValueError('Btrfs subvolumes/default differ from the reviewed layout')
        subprocess.run(['btrfs','restore','-m','-S','-r','5',str(image),str(self.top)],
                       check=True,capture_output=True,text=True)

    def path(self, path):
        safe_path(path)
        for name, mount in sorted(self.subvolumes.items(),key=lambda item:len(item[1]),reverse=True):
            if mount == '/' or path == mount or path.startswith(mount+'/'):
                relative = path.lstrip('/') if mount == '/' else path[len(mount):].lstrip('/')
                selected = self.top/name/relative
                # Parent symlinks must never escape the restored image tree.
                if not selected.parent.resolve().is_relative_to(self.top.resolve()):
                    raise ValueError('Image path escapes its restored filesystem')
                return selected
        raise ValueError('Unmapped image path')

    def read_file(self, path, allow_empty=False):
        selected = self.path(path)
        if (not selected.is_file() or selected.is_symlink()
                or (not allow_empty and not selected.stat().st_size)):
            raise ValueError('Missing/empty regular image file: '+path)
        return selected

    def stats(self, paths):
        records = {}
        for path in paths:
            selected = self.path(path)
            try:
                mode = selected.lstat().st_mode
            except FileNotFoundError as error:
                raise ValueError('Missing image inode: '+path) from error
            kind = 'symlink' if stat.S_ISLNK(mode) else 'directory' if stat.S_ISDIR(mode) else 'regular' if stat.S_ISREG(mode) else 'special'
            record = 'Type: '+kind
            if kind == 'symlink':
                record += '\nFast link dest: "'+os.readlink(selected)+'"'
            records[path] = kind, record
        return records

    def exists(self, path):
        selected = self.path(path)
        return selected.exists() or selected.is_symlink()

    def listdir(self, path):
        return [entry.name for entry in self.path(path).iterdir()]

    def link_target(self, path):
        return os.readlink(self.path(path))

    def filesystem_uuid(self):
        data = subprocess.check_output(['btrfs','inspect-internal','dump-super',str(self.image)],text=True)
        values = re.findall(r'^fsid\s+([0-9a-fA-F-]+)$',data,re.M)
        if len(values) != 1:
            raise ValueError('Missing/ambiguous Btrfs filesystem UUID')
        return uuid.UUID(values[0])


def open_image(image, temporary):
    filesystem = subprocess.check_output(['blkid','-p','-o','value','-s','TYPE',str(image)],text=True).strip()
    if filesystem == 'vfat':
        return FatImage(image, temporary)
    if filesystem == 'btrfs':
        return BtrfsImage(image, temporary)
    if filesystem == 'ext4':
        return Ext4Image(image, temporary)
    raise ValueError('Unsupported image filesystem: '+filesystem)


def require_types(reader, paths, kind='regular'):
    records = reader.stats(paths)
    for path, (actual, _) in records.items():
        if actual != kind:
            raise ValueError(f'Expected {kind} image inode: {path}')
    return records


def inspect_readers(root, boot, layout, grub_profile, primary='debug', manager='grub'):
    for reader, key in ((root, 'root_filesystem_uuid'), (boot, 'boot_filesystem_uuid')):
        expected = layout[key].upper() if reader.filesystem == 'vfat' else uuid.UUID(layout[key])
        if reader.filesystem_uuid() != expected:
            raise ValueError(f'Image filesystem UUID differs from measured {key}')
    if manager == 'limine':
        if boot.filesystem != 'vfat':
            raise ValueError('Limine snapshot boot storage must be FAT')
        sections = boot_checks.uki.read_sections(boot.read_file('/EFI/Linux/omarchy.efi'),
                                                filesystem=root.filesystem)
        cmdline = sections[b'.cmdline'].rstrip(b'\0\n').decode('ascii')
        if boot.read_file('/limine.conf').read_text() != default_config(cmdline, slot=primary):
            raise ValueError('Image Limine menu differs from the reviewed initial Nabu menu')
        entries = {'Omarchy Nabu': '/EFI/Linux/omarchy.efi'}
    elif manager == 'grub':
        menu = boot.read_file('/grub2/grub.cfg').read_text()
        if menu != menu_config(layout, grub_profile, primary):
            raise ValueError('Image GRUB menu differs from the reviewed Nabu menu')
        entries = {label: '/EFI/arch/'+filename for label, filename in
                   boot_checks.parse_menu(menu, layout['boot_filesystem_uuid'], primary).items()}
        redirect = (f"search --no-floppy --fs-uuid --set=omarchy_boot {layout['boot_filesystem_uuid']}\n"
                    'configfile ($omarchy_boot)/grub2/grub.cfg\n')
        if root.read_file('/boot/grub2/grub.cfg').read_text() != redirect:
            raise ValueError('Underlying root GRUB redirect targets a different boot filesystem')
    else:
        raise ValueError('Unsupported boot manager: '+manager)
    reports = []
    for label, uki_path in entries.items():
        filename = PurePosixPath(uki_path).name
        require_types(boot, [uki_path])
        sections = boot_checks.uki.read_sections(boot.read_file(uki_path),filesystem=root.filesystem)
        release = sections[b'.uname'].rstrip(b'\0\n').decode('ascii')
        kernel_path = '/vmlinux-'+release
        dtb_path = '/dtb-'+release
        require_types(boot, [kernel_path, dtb_path])
        if boot.read_file(kernel_path).read_bytes() != sections[b'.linux']:
            raise ValueError(f'Embedded kernel differs from matching boot image: {label}')
        if boot.read_file(dtb_path).read_bytes() != sections[b'.dtb']:
            raise ValueError(f'Embedded DTB differs from matching device tree: {label}')
        compiled = filename != 'arch-linux-nabu-stock.efi'
        config_path = ('/etc/kernel/uki-nabu-'+primary+'.conf' if compiled else '/etc/kernel/uki.conf')
        config = root.read_file(config_path).read_text()
        configured = re.findall(r'^DeviceTree=(/boot/dtb-[A-Za-z0-9._+-]+|/usr/lib/modules/[A-Za-z0-9._+-]+/nabu[.]dtb)$', config, re.M)
        if len(configured) != 1:
            raise ValueError(f'Missing/ambiguous configured DTB: {label}')
        target = configured[0].removeprefix('/boot')
        if target.startswith('/usr/lib/modules/'):
            if target != '/usr/lib/modules/'+release+'/nabu.dtb':
                raise ValueError('Configured root DTB release differs from UKI')
            kernel = '/usr/lib/modules/'+release+'/vmlinuz'
            require_types(root, [target, kernel])
            if root.read_file(kernel).read_bytes() != sections[b'.linux']:
                raise ValueError('Root kernel differs from UKI')
            if root.read_file(target).read_bytes() != sections[b'.dtb']:
                raise ValueError('Root DTB differs from UKI')
        elif target != dtb_path:
            # Stock uses one reviewed relative symlink; never follow arbitrary paths.
            if target != '/dtb-linux-nabu':
                raise ValueError(f'Configured DTB does not match UKI release: {label}')
            kind, record = boot.stats([target])[target]
            links = re.findall(r'^Fast link dest: "([^"]+)"$', record, re.M)
            if kind != 'symlink' or links != [dtb_path.removeprefix('/')]:
                raise ValueError(f'Stock DTB alias does not match UKI release: {label}')
        modules = '/usr/lib/modules/'+release
        require_types(root, [modules+'/kernel'], 'directory')
        require_types(root, [modules+'/modules.dep'])
        # Our compiled package carries its config; the stock package need not.
        if compiled:
            require_types(root, [modules+'/config'])
            config = root.read_file(modules+'/config').read_text().splitlines()
            if 'CONFIG_ARM64=y' not in config:
                raise ValueError(f'Matching ARM64 compiled config missing: {label}')
        paths = boot_checks.module_paths(root.read_file(modules+'/modules.dep').read_text())
        require_types(root, [modules+'/'+path for path in paths])
        reports.append({'entry': label, 'uki': filename, 'release': release,
                        'module_files': len(paths), 'kernel_dtb_match': True})
    return reports


def inspect_images(rootfs, boot_image, layout, grub_profile, primary='debug', manager='grub'):
    with tempfile.TemporaryDirectory(prefix='nabu-image-boot-') as directory:
        root_tmp = Path(directory)/'root'
        boot_tmp = Path(directory)/'boot'
        root_tmp.mkdir(); boot_tmp.mkdir()
        return inspect_readers(open_image(rootfs, root_tmp), open_image(boot_image, boot_tmp),
                               layout, grub_profile, primary, manager)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--boot', type=Path, required=True)
    parser.add_argument('--primary', choices=('debug', 'local'), default='debug')
    parser.add_argument('--boot-manager', choices=('grub', 'limine'), default='grub')
    args = parser.parse_args()
    reports = inspect_images(args.rootfs, args.boot, load_layout(),
                             json.loads((ROOT/'profiles/nabu/grub.json').read_text()), args.primary, args.boot_manager)
    print(json.dumps({'boot_image_checks': reports, 'physical_boot_tested': False}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Image boot checks failed: {exc}')
