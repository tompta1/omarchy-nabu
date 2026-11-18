#!/usr/bin/env python3
"""Check image files offline, without loop mounts or attached hardware."""
import hashlib
from pathlib import Path
import struct
import subprocess
import tempfile
import re
import stat
import argparse
import json
import shutil
from nabu_layout import load_layout
from nabu_image_boot import open_image, inspect_readers, boot_checks
from nabu_notices import manifest as notice_manifest
from nabu_image_options import INSTALLED as OPTIONS_PATH, load_options, validate as validate_options, encoded as encode_options
from nabu_desktop import OPTIONAL_FILES, SCREENSAVER_FILES, SETUP_LINK
import nabu_screensaver as screensaver
from nabu_kernel import select as select_kernel, commandline as kernel_commandline
from nabu_image_options import kernel_cmdline as board_commandline

project = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--images', type=Path, default=project / 'out/images')
parser.add_argument('--debug', '--compiled', dest='debug', action='store_true', help='Verify the selected compiled kernel and stock fallback')
parser.add_argument('--boot', type=Path, help='Separate /boot image')
parser.add_argument('--boot-manager', choices=('grub', 'limine'), default='grub')
args = parser.parse_args()
if args.boot_manager == 'limine' and (not args.boot or not args.debug):
  parser.error('Limine verification requires --boot and --compiled')
images = args.images
rootfs = images / 'rootfs.img'
esp = images / 'esp.img'
root_type = subprocess.check_output(['blkid','-p','-o','value','-s','TYPE',str(rootfs)],text=True).strip()
if args.boot and root_type != 'btrfs':
  raise SystemExit('Maintained images require a Btrfs root')
if root_type == 'btrfs':
  subprocess.run(['btrfs','check','--readonly','--check-data-csum',str(rootfs)],check=True)
else:
  subprocess.run(['e2fsck', '-fn', str(rootfs)], check=True)
if args.boot:
  boot_type = subprocess.check_output(['blkid','-p','-o','value','-s','TYPE',str(args.boot)],text=True).strip()
  if boot_type not in ('ext4', 'vfat'):
    raise SystemExit('Unsupported boot filesystem')
  subprocess.run(['fsck.fat', '-n', str(args.boot)] if boot_type == 'vfat' else
                 ['e2fsck', '-fn', str(args.boot)], check=True)
else:
  subprocess.run(['fsck.fat', '-n', str(esp)], check=True)

with tempfile.TemporaryDirectory(prefix='nabu-verify-') as directory:
  temporary = Path(directory)
  root_tmp = temporary/'root-reader'; root_tmp.mkdir()
  root_reader = open_image(rootfs, root_tmp)
  boot_tmp = temporary/'boot-reader'; boot_tmp.mkdir()
  boot_reader = open_image(args.boot, boot_tmp) if args.boot else None
  def extract(path, name, allow_empty=False):
    output = temporary / name
    reader = root_reader
    if args.boot and path.startswith('/boot/'):
      reader = boot_reader
      path = path[len('/boot'):]
    shutil.copyfile(reader.read_file(path, allow_empty=allow_empty), output)
    return output

  for path, name in [('/usr/lib/systemd/systemd', 'systemd'),
                     ('/usr/local/bin/wvkbd-mobintl', 'keyboard'),
                     ('/usr/bin/cam', 'camera-discovery-tool')]:
    binary = extract(path, name).read_bytes()[:64]
    if binary[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', binary, 18)[0] != 183:
      raise SystemExit(f'Not an AArch64 ELF executable: {path}')
  license_sources = {'LICENSE': 'GPL-3.0.txt', 'COPYING': 'wvkbd-COPYING.txt',
                     'COPYING_WESTON': 'wvkbd-COPYING-WESTON.txt'}
  source_root = Path(__file__).resolve().parent.parent
  for installed, source in license_sources.items():
    notice = extract('/usr/local/share/licenses/wvkbd/'+installed, 'wvkbd-'+installed)
    if notice.read_bytes() != (source_root/'licenses'/source).read_bytes():
      raise SystemExit(f'wvkbd notice differs from reviewed source: {installed}')
  def present(path):
    return root_reader.exists(path)

  metadata = None
  home = '/home/tablet'
  compiled_slot = 'debug'
  if args.boot:
    metadata = json.loads(extract('/etc/omarchy-nabu/install.json', 'install.json').read_text())
    username = metadata.get('username', '')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,30}', username) or metadata.get('boot_profile') != 'existing-uboot':
      raise SystemExit('Invalid local provisioning metadata')
    if type(metadata.get('ssh_enabled')) is not bool or type(metadata.get('autologin')) is not bool:
      raise SystemExit('Provisioning choices must be explicit booleans')
    home = '/home/' + username
    options_file = extract('/'+OPTIONS_PATH, 'image-options.json')
    options = validate_options(json.loads(options_file.read_text()))
    if options != load_options() or options_file.read_bytes() != encode_options(options):
      raise SystemExit('Installed image options differ from the selected build profile')
    kernel_selection = json.loads(extract('/etc/omarchy-nabu/kernel.json', 'kernel.json').read_text())
    if kernel_selection != select_kernel():
      raise SystemExit('Installed compiled kernel selection differs from build inputs')
    compiled_slot = kernel_selection['slot']
    for index, (installed, source) in enumerate((OPTIONAL_FILES | SCREENSAVER_FILES).items()):
      selected = options['display_controls'] if installed in OPTIONAL_FILES else options['screensaver_touch_dismiss']
      if selected:
        if extract('/'+installed, 'display-option-'+str(index)).read_bytes() != (project/source).read_bytes():
          raise SystemExit('Selected display recipe differs from reviewed source: '+installed)
      elif present('/'+installed):
        raise SystemExit('Unselected display recipe entered the default image: '+installed)
    if options['display_controls']:
      if root_reader.link_target('/'+SETUP_LINK) != '../omarchy-nabu-display-setup.service':
        raise SystemExit('Selected first-login display setup is not enabled')
    elif present('/'+SETUP_LINK) or present(home+'/.config/omarchy/plugins/nabu.display'):
      raise SystemExit('Unselected user display setup entered the default image')
    expected_notices = notice_manifest(project)
    installed_notices = json.loads(extract('/usr/share/omarchy-nabu/notices.json', 'notices.json').read_text())
    if installed_notices != expected_notices:
      raise SystemExit('Known source-notice manifest differs from reviewed inputs')
    for index, notice in enumerate(expected_notices['notices']):
      data = extract('/'+notice['path'], 'notice-'+str(index)).read_bytes()
      if hashlib.sha256(data).hexdigest() != notice['sha256']:
        raise SystemExit('Known source notice is missing or changed: '+notice['path'])
    accounts = extract('/etc/passwd', 'passwd').read_text().splitlines()
    if not any(row.startswith(username+':') and row.split(':')[2] == '1000' for row in accounts):
      raise SystemExit('Configured desktop account is missing or has the wrong UID')
    greeter = [row.split(':') for row in accounts if row.startswith('sddm:')]
    if len(greeter) != 1 or len(greeter[0]) != 7:
      raise SystemExit('Image is missing the SDDM service account')
    greeter_owner = tuple(map(int, greeter[0][2:4]))
    for item, expected_type in (('/var/lib/sddm/.config', stat.S_ISDIR),
                                ('/var/lib/sddm/.config/kwinrc', stat.S_ISREG)):
      details = root_reader.path(item).lstat()
      if (not expected_type(details.st_mode) or (details.st_uid, details.st_gid) != greeter_owner
          or not details.st_mode & stat.S_IWUSR):
        raise SystemExit(f'Image greeter configuration is not writable by SDDM: {item}')
    machine_id = extract('/etc/machine-id', 'machine-id', allow_empty=True)
    if machine_id.read_bytes():
      raise SystemExit('Image contains a build-time machine identity')
    if any(name.startswith('ssh_host_') for name in root_reader.listdir('/etc/ssh')):
      raise SystemExit('Image contains baked SSH host keys')
    for path in ('/root/.ssh/authorized_keys', '/.omarchy-nabu-build-root', '/var/lib/dbus/machine-id'):
      if present(path): raise SystemExit(f'Build-time identity or marker left behind: {path}')
    root_listing = root_reader.listdir('/')
    if any(re.fullmatch(r'qemu_.*\.core|core',name) for name in root_listing):
      raise SystemExit('Image contains a build-time core dump')
    ssh_policy = extract('/etc/ssh/sshd_config.d/90-nabu-key-only.conf', 'sshd-policy').read_text()
    for setting in ('PermitRootLogin no', 'PasswordAuthentication no', 'KbdInteractiveAuthentication no'):
      if setting not in ssh_policy.splitlines(): raise SystemExit('Key-only SSH policy is missing')
    if metadata['ssh_enabled']:
      extract(home+'/.ssh/authorized_keys', 'authorized_keys')
      if not present('/etc/systemd/system/multi-user.target.wants/sshd.service'):
        raise SystemExit('Key-only SSH was requested but is not enabled')
      ssh_unit = extract('/usr/lib/systemd/system/sshd.service', 'sshd.service').read_text()
      extract('/usr/lib/systemd/system/sshdgenkeys.service', 'sshdgenkeys.service')
      if 'sshdgenkeys.service' not in ssh_unit: raise SystemExit('SSH first-boot key generation is not wired')
    elif present(home+'/.ssh/authorized_keys') or present('/etc/systemd/system/multi-user.target.wants/sshd.service'):
      raise SystemExit('SSH was enabled without requested public-key provisioning')
    login = extract('/etc/sddm.conf.d/90-nabu.conf', 'sddm.conf').read_text()
    if metadata['autologin']:
      if f'User={username}\n' not in login or 'Session=omarchy\n' not in login:
        raise SystemExit('Requested desktop autologin is not configured')
    elif '[Autologin]' in login:
      raise SystemExit('Desktop autologin is enabled without being requested')
    if root_reader.link_target('/etc/resolv.conf') != '/run/systemd/resolve/stub-resolv.conf':
      raise SystemExit('Runtime resolver symlink was replaced by build-time DNS')
    browser = extract(home+'/.config/mimeapps.list', 'mimeapps.list').read_text()
    for kind in ('x-scheme-handler/http', 'x-scheme-handler/https', 'text/html'):
      if f'{kind}=chromium.desktop' not in browser.splitlines():
        raise SystemExit('Default browser differs from the reviewed Omarchy release')
    extract('/usr/share/applications/chromium.desktop', 'browser.desktop')
    extract('/usr/bin/chromium', 'chromium')
    from nabu_repositories import apply_policy, load_policy
    policy = load_policy(Path(__file__).resolve().parent.parent/'profiles/nabu/repositories.json')
    config = extract('/etc/pacman.conf', 'pacman.conf').read_text()
    if config != apply_policy(config, policy):
      raise SystemExit('Image repository order/channel or hardware holds differ from policy')
    if any(re.fullmatch(r'\s*DisableSandbox(?:Filesystem|Syscalls)?\s*(?:#.*)?', line)
           for line in config.splitlines()):
      raise SystemExit('Image retains a global build-time pacman sandbox bypass')
    for source, installed, name in (
        ('scripts/omarchy-nabu-pacman-sandbox', '/usr/local/libexec/omarchy-nabu-pacman-sandbox', 'sandbox-helper'),
        ('overlay/etc/systemd/system/omarchy-nabu-pacman-sandbox.service',
         '/etc/systemd/system/omarchy-nabu-pacman-sandbox.service', 'sandbox-unit')):
      if extract(installed, name).read_bytes() != (project/source).read_bytes():
        raise SystemExit('Image pacman sandbox selector differs from reviewed source')
    if not present('/etc/systemd/system/multi-user.target.wants/omarchy-nabu-pacman-sandbox.service'):
      raise SystemExit('Native first-boot pacman sandbox selection is not enabled')
    if extract('/usr/local/bin/omarchy-nabu-iris-recover', 'iris-recovery-helper').read_bytes() != (project/'scripts/omarchy-nabu-iris-recover').read_bytes():
      raise SystemExit('Iris recovery helper differs from reviewed source')
    audio_rule = '/etc/wireplumber/wireplumber.conf.d/90-nabu-iris-codec.conf'
    if extract(audio_rule, '90-nabu-iris-codec.conf').read_bytes() != (project/'install-overlay'/audio_rule.lstrip('/')).read_bytes():
      raise SystemExit('Image is missing the WirePlumber Iris camera-discovery rule')
    iris_options = '/etc/modprobe.d/90-nabu-iris.conf'
    if extract(iris_options, '90-nabu-iris.conf').read_bytes() != (project/'install-overlay'/iris_options.lstrip('/')).read_bytes():
      raise SystemExit('Image is missing the Nabu Iris firmware boot option')
    wifi_unit='/etc/systemd/system/omarchy-nabu-wifi-shutdown.service'
    if extract(wifi_unit, 'wifi-shutdown.service').read_bytes() != (project/'overlay'/wifi_unit.lstrip('/')).read_bytes():
      raise SystemExit('Image Wi-Fi shutdown ordering differs from reviewed source')
    if not present('/etc/systemd/system/graphical.target.wants/omarchy-nabu-wifi-shutdown.service'):
      raise SystemExit('Nabu early Wi-Fi shutdown is not enabled')
    if present('/etc/systemd/system/omarchy-nabu-wifi-shutdown-test.service'):
      raise SystemExit('Image contains the historical shutdown experiment')
    import importlib.machinery
    import importlib.util
    compat_loader = importlib.machinery.SourceFileLoader('shell_compat', str(project/'scripts/omarchy-nabu-shell-compat'))
    compat_spec = importlib.util.spec_from_loader(compat_loader.name, compat_loader)
    compat = importlib.util.module_from_spec(compat_spec)
    compat_loader.exec_module(compat)
    expected_foot = screensaver.FOOT_REPLACEMENT if options['screensaver_touch_dismiss'] else screensaver.FOOT_ORIGINAL
    if extract('/'+screensaver.FOOT_RELATIVE, 'foot-screensaver.ini').read_text() != expected_foot:
      raise SystemExit('Foot screensaver differs from selected image behavior')
    for relative, replacement in ((compat.RELATIVE, compat.REPLACEMENT), (compat.MEDIA_RELATIVE, compat.MEDIA_REPLACEMENT)):
      if replacement not in extract('/'+relative, Path(relative).name).read_text():
        raise SystemExit('Required guarded rotation fix is missing: '+relative)
    if extract('/usr/local/libexec/omarchy-nabu-shell-compat', 'shell-compat-helper').read_bytes() != (project/'scripts/omarchy-nabu-shell-compat').read_bytes():
      raise SystemExit('Shell compatibility update helper differs from reviewed source')
    if extract('/usr/local/libexec/nabu_image_options.py', 'runtime-image-options.py').read_bytes() != (project/'scripts/nabu_image_options.py').read_bytes():
      raise SystemExit('Runtime image option reader differs from reviewed source')
    hook='etc/pacman.d/hooks/95-omarchy-nabu-shell-compat.hook'
    if extract('/'+hook, 'shell-compat.hook').read_bytes() != (project/'overlay'/hook).read_bytes():
      raise SystemExit('Rotation/screensaver update hook differs from reviewed source')
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader('pacman_sandbox', str(project/'scripts/omarchy-nabu-pacman-sandbox'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    sandbox = importlib.util.module_from_spec(spec)
    loader.exec_module(sandbox)
    generated = extract('/etc/pacman.d/omarchy-nabu-sandbox.conf', 'sandbox.conf').read_text()
    abi_match = re.search(r'^# Landlock ABI ([1-9][0-9]*):', generated, re.M)
    if generated != sandbox.render(int(abi_match[1]) if abi_match else 0):
      raise SystemExit('Invalid generated pacman sandbox compatibility options')
    mirror = extract('/etc/pacman.d/mirrorlist', 'mirrorlist').read_text()
    if 'https://arm-stable-mirror.omarchy.org/$arch/$repo' not in mirror or '/edge/' in config:
      raise SystemExit('Image has inconsistent ARM package channels')
    local_packages = json.loads(extract('/usr/share/omarchy-nabu/source-packages.json', 'source-packages.json').read_text())
    if set(local_packages['packages']) != set(policy['source_packages']):
      raise SystemExit('Source-built device package receipt is incomplete')
    current_recipes = {name: hashlib.sha256((project/'packages'/name/'PKGBUILD').read_bytes()).hexdigest()
                       for name in policy['source_packages']}
    if local_packages.get('recipes') != current_recipes:
      raise SystemExit('Image contains device packages built from different recipes')
    for name, version in local_packages['packages'].items():
      if not re.fullmatch(r'[a-z0-9+_.-]+', name) or not re.fullmatch(r'[A-Za-z0-9+_.:-]+', version):
        raise SystemExit('Invalid source package receipt')
      extract(f'/var/lib/pacman/local/{name}-{version}/desc', name+'-installed.desc')
    if present('/opt/nabu/efi-template') or present('/opt/nabu/fastboot-script'):
      raise SystemExit('Legacy boot/flash payloads entered the existing-U-Boot image')
  for path, name in [('/etc/fstab', 'fstab'),
                     (home + '/.config/hypr/monitors.lua', 'monitors.lua'),
                     ('/usr/local/share/wayland-sessions/omarchy.desktop', 'session.desktop'),
                     ('/usr/local/libexec/omarchy-nabu-keyring-init', 'keyring-init'),
                     ('/etc/mkinitcpio.conf.d/zz-nabu.conf', 'initramfs.conf')]:
    extract(path, name)
  if args.boot_manager == 'limine':
    if root_reader.link_target('/etc/pacman.d/hooks/90-linux-nabu.uki.hook') != '/dev/null':
      raise SystemExit('Legacy UKI hook overlaps upstream Limine maintenance')
  else:
    extract('/etc/pacman.d/hooks/90-linux-nabu.uki.hook', 'kernel-hook')
  fstab = (temporary / 'fstab').read_text()
  if args.boot:
    layout = load_layout()
    from nabu_filesystems import fstab as expected_fstab
    if fstab != expected_fstab(layout):
      raise SystemExit('Btrfs subvolume/filesystem mounts differ from the reviewed layout')
  elif 'PARTLABEL=linux' not in fstab:
    raise SystemExit('Nabu root partition mapping is missing')
  hooks = next(line for line in (temporary / 'initramfs.conf').read_text().splitlines()
               if line.startswith('HOOKS='))
  hook_names = hooks.removeprefix('HOOKS=(').removesuffix(')').split()
  if 'systemd' not in hook_names or any(token in hook_names for token in ('autodetect', 'encrypt', 'btrfs')):
    raise SystemExit('Nabu initramfs baseline was overridden')
  if args.boot_manager == 'limine':
    if any(name.startswith('linux-nabu-headers-') for name in root_reader.listdir('/var/lib/pacman/local')):
      raise SystemExit('Limine image still carries headers for the retired stock kernel')
    if (temporary/'initramfs.conf').read_bytes() != (project/'profiles/nabu/mkinitcpio-limine.conf').read_bytes():
      raise SystemExit('Snapshot initramfs differs from the reviewed systemd hook configuration')
    tuner_unit = '/etc/systemd/system/omarchy-nabu-tuning.service'
    if extract(tuner_unit, 'tuning.service').read_bytes() != (project/'overlay'/tuner_unit.lstrip('/')).read_bytes():
      raise SystemExit('Early Nabu tuning service differs from the reviewed coldplug ordering')
    if not present('/etc/systemd/system/sysinit.target.wants/omarchy-nabu-tuning.service'):
      raise SystemExit('Early Nabu tuning service is not enabled')
    from nabu_limine import runtime_config
    board = board_commandline((project/'profiles/nabu/kernel.cmdline').read_text(), options)
    expected_runtime = runtime_config(compiled_slot, kernel_commandline(board, kernel_selection))
    if extract('/etc/default/limine','runtime-limine.conf').read_text() != expected_runtime:
      raise SystemExit('Runtime Limine configuration differs from the reviewed board/kernel policy')
    for installed, source in (
        ('/usr/local/libexec/omarchy-nabu-snapshot-init','scripts/omarchy-nabu-snapshot-init'),
        ('/etc/systemd/system/omarchy-nabu-snapshot-init.service','profiles/nabu/omarchy-nabu-snapshot-init.service')):
      if extract(installed,'snapshot-'+Path(installed).name).read_bytes() != (project/source).read_bytes():
        raise SystemExit('Snapshot initialization differs from reviewed source')
    if not present('/etc/systemd/system/multi-user.target.wants/omarchy-nabu-snapshot-init.service'):
      raise SystemExit('First-boot snapshot initialization is not enabled')
    root_guard = (project/'profiles/nabu/require-btrfs-root.conf').read_bytes()
    for unit in ('systemd-remount-fs.service', 'systemd-growfs-root.service'):
      installed = f'/etc/systemd/system/{unit}.d/10-nabu-btrfs-root.conf'
      if extract(installed, unit+'-root-guard').read_bytes() != root_guard:
        raise SystemExit(f'{unit} must run only on the directly mounted Btrfs root')
    if present('/var/lib/omarchy-nabu/snapshot-ready'):
      raise SystemExit('Image contains a build-time snapshot initialization marker')
    for path in ('/usr/lib/initcpio/install/sd-btrfs-overlayfs',
                 '/usr/lib/limine/overlayfs-setup',
                 '/usr/lib/systemd/system/overlayfs-setup.service',
                 '/usr/bin/limine-snapper-sync', '/usr/bin/limine-snapper-restore',
                 '/usr/bin/limine-update', '/usr/bin/snapper'):
      extract(path, 'snapshot-'+Path(path).name)
  if present('/etc/pacman.d/gnupg'):
    raise SystemExit('Build-time pacman keyring was left in the image')

  uki_name = 'arch-linux-nabu-'+compiled_slot+'.efi' if args.debug else 'arch-linux-nabu.efi'
  uki_dir = '/boot/EFI/arch/' if args.boot else '/boot/efi/EFI/arch/'
  uki = extract('/boot/EFI/Linux/omarchy.efi' if args.boot_manager == 'limine' else
                uki_dir + uki_name, 'root-uki')
  if not args.boot:
    esp_uki = temporary / 'esp-uki'
    subprocess.run(['mcopy', '-i', str(esp), '::/EFI/arch/' + uki_name, str(esp_uki)], check=True)
    if hashlib.sha256(uki.read_bytes()).digest() != hashlib.sha256(esp_uki.read_bytes()).digest():
      raise SystemExit('ESP and rootfs contain different kernel images')
  validated = boot_checks.uki.read_sections(uki, root='PARTLABEL=userdata' if args.boot else 'PARTLABEL=linux',
                                          require_ext4=bool(args.boot),filesystem=root_type)
  sections = {name.decode(): value for name, value in validated.items()}
  if (args.boot and args.boot_manager == 'grub'
      and (b'logo.nologo' in sections['.cmdline'].split()) != options['hide_kernel_logos']):
    raise SystemExit('Kernel logo preference differs from selected image options')
  if args.debug:
    if any(token in sections['.cmdline'].split() for token in (b'earlycon=efifb', b'keep_bootcon')):
      raise SystemExit('EFI early-console arguments caused a confirmed Nabu boot panic')
    if args.boot:
      board = board_commandline((project/'profiles/nabu/kernel.cmdline').read_text(), options)
      expected_cmdline = kernel_commandline(board, kernel_selection).encode().split()
      if sections['.cmdline'].rstrip(b'\0').split() != expected_cmdline:
        raise SystemExit('Compiled kernel command line differs from board, image and kernel policies')
      if sections['.uname'].rstrip(b'\0\n').decode() != kernel_selection['release']:
        raise SystemExit('Compiled UKI release differs from selected kernel')
    else:
      for argument in (b'console=tty0', b'initcall_debug', b'log_buf_len=8M', b'ignore_loglevel', b'panic=0'):
        if argument not in sections['.cmdline'].split():
          raise SystemExit(f'Debug kernel command line is missing {argument!r}')
    if args.boot_manager == 'grub':
      stock = extract(uki_dir + 'arch-linux-nabu-stock.efi', 'stock-uki')
      if args.boot:
        stock_sections = boot_checks.uki.read_sections(stock, root='PARTLABEL=userdata',filesystem=root_type)
        if (b'logo.nologo' in stock_sections[b'.cmdline'].split()) != options['hide_kernel_logos']:
          raise SystemExit('Stock kernel logo preference differs from selected image options')
    if args.boot:
      release = sections['.uname'].rstrip(b'\0\n').decode()
      if not re.fullmatch(r'[A-Za-z0-9._+-]+', release):
        raise SystemExit('Invalid kernel release in UKI')
      config = extract('/usr/lib/modules/'+release+'/config', 'running-kernel.config').read_text()
      if 'CONFIG_SECURITY_LANDLOCK=y' not in config.splitlines():
        raise SystemExit('New U-Boot debug image must include the Landlock-enabled kernel')
      if 'CONFIG_BTRFS_FS=y' not in config.splitlines():
        raise SystemExit('Normal image kernel must include built-in Btrfs support')
      if args.boot_manager == 'grub':
        grub = extract('/boot/grub2/grub.cfg', 'grub.cfg').read_text()
        from nabu_grub import kernel_entries
        if any("menuentry '"+label+"'" not in grub for label in kernel_entries(compiled_slot)):
          raise SystemExit('Compiled/fallback GRUB menu is missing')
      extract('/usr/local/libexec/omarchy-nabu-usb-gadget', 'usb-gadget')
    else:
      stock_esp = temporary / 'stock-esp-uki'
      subprocess.run(['mcopy', '-i', str(esp), '::/EFI/arch/arch-linux-nabu-stock.efi', str(stock_esp)], check=True)
      if stock.read_bytes() != stock_esp.read_bytes():
        raise SystemExit('Stock fallback kernel differs between ESP and rootfs')
      refind = extract('/boot/efi/EFI/BOOT/refind.conf', 'refind.conf').read_text()
      if 'default_selection "Omarchy Nabu debug"' not in refind or 'Omarchy Nabu stock fallback' not in refind:
        raise SystemExit('Debug/fallback boot menu is missing')
  config_path = '/etc/kernel/uki-nabu-'+compiled_slot+'.conf' if args.debug else '/etc/kernel/uki.conf'
  uki_config = extract(config_path, 'uki.conf').read_text()
  dtb_path = re.search(r'^DeviceTree=(/boot/dtb-[a-zA-Z0-9._+-]+|/usr/lib/modules/[a-zA-Z0-9._+-]+/nabu[.]dtb)$', uki_config, re.M)
  if not dtb_path:
    raise SystemExit('UKI configuration does not identify the Nabu DTB')
  if extract(dtb_path[1], 'nabu.dtb').read_bytes() != sections['.dtb']:
    raise SystemExit('UKI does not embed the configured Nabu device tree')

  if args.boot:
    for report in inspect_readers(root_reader, boot_reader, load_layout(),
                                 json.loads((project/'profiles/nabu/grub.json').read_text()), compiled_slot, args.boot_manager):
      print(f"Verified {report['entry']}: {report['release']}, {report['module_files']} module paths")

print('Offline checks passed: filesystems, ARM64 userspace/keyboard/UKI, Nabu DTB, root mapping and selected boot menu and matching kernel payloads/modules.')
print('Physical boot and hardware behaviour have not been tested.')
