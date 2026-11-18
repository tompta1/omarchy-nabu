#!/bin/bash
set -euo pipefail
if [[ ${_NABU_BUILD_SCRIPT_LOADED:-} != "$0" ]]; then
  export _NABU_BUILD_SCRIPT_LOADED="$0"
  exec bash -c "$(cat -- "$0")" "$0" "$@"
fi
unset _NABU_BUILD_SCRIPT_LOADED
[[ -f /.omarchy-nabu-build-root && -d /build/install-overlay ]] || exit 1
output=$(python3 /build/scripts/nabu_paths.py output)
mkdir -p "$output"
python3 /build/scripts/nabu_layout.py >/dev/null
python3 /build/scripts/nabu_provision.py >/dev/null
bash /build/scripts/apply-root-overlay.sh /build/install-overlay /
# The SDDM package creates this configuration before sysusers creates its account.
greeter_config=/var/lib/sddm/.config
if [[ -d $greeter_config && ! -L $greeter_config && -f $greeter_config/kwinrc && ! -L $greeter_config/kwinrc ]]; then
  chown --no-dereference sddm:sddm "$greeter_config" "$greeter_config/kwinrc"
fi
install -Dm755 /build/scripts/nabu_tuning.py /usr/local/libexec/omarchy-nabu-tuning
install -Dm644 /build/overlay/etc/systemd/system/omarchy-nabu-tuning.service /etc/systemd/system/omarchy-nabu-tuning.service
/usr/local/libexec/omarchy-nabu-tuning --kernel-config "$(python3 /build/scripts/nabu_paths.py kernel)/config"
systemctl enable omarchy-nabu-tuning.service
python3 /build/scripts/install-license-notices.py
if [[ ! -e /etc/vconsole.conf ]]; then
  install -Dm644 /build/overlay/etc/vconsole.conf /etc/vconsole.conf
fi
# Prepared-root reuse skips install-root.sh, so refresh this runtime integration
# here too before selecting the new compiled kernel.
install -Dm755 /build/scripts/omarchy-nabu-pacman-sandbox /usr/local/libexec/omarchy-nabu-pacman-sandbox
install -Dm755 /build/scripts/nabu_repositories.py /usr/local/libexec/omarchy-nabu-repositories
install -Dm644 /build/profiles/nabu/repositories.json /usr/share/omarchy-nabu/repositories.json
install -Dm755 /build/overlay/usr/local/libexec/omarchy-nabu-pacman-restore /usr/local/libexec/omarchy-nabu-pacman-restore
install -Dm644 /build/overlay/etc/systemd/system/omarchy-nabu-pacman-sandbox.service /etc/systemd/system/omarchy-nabu-pacman-sandbox.service
/usr/local/libexec/omarchy-nabu-pacman-restore
sed -i '/^DisableSandboxSyscalls$/d; /^DisableSandboxFilesystem$/d' /etc/pacman.conf
systemctl enable omarchy-nabu-pacman-sandbox.service
# Apply to reused prepared roots as well as freshly installed roots.
install -Dm644 /build/overlay/etc/systemd/system/omarchy-nabu-wifi-shutdown.service \
  /etc/systemd/system/omarchy-nabu-wifi-shutdown.service
systemctl enable omarchy-nabu-wifi-shutdown.service
bash /build/scripts/ensure-camera-tools.sh
install -Dm755 /build/scripts/omarchy-nabu-iris-recover /usr/local/bin/omarchy-nabu-iris-recover
# Refresh audited keyboard notices when reusing an older prepared root.
install -Dm644 /build/licenses/GPL-3.0.txt /usr/local/share/licenses/wvkbd/LICENSE
install -Dm644 /build/licenses/wvkbd-COPYING.txt /usr/local/share/licenses/wvkbd/COPYING
install -Dm644 /build/licenses/wvkbd-COPYING-WESTON.txt /usr/local/share/licenses/wvkbd/COPYING_WESTON
python3 /build/scripts/nabu_desktop.py
/usr/local/libexec/omarchy-nabu-shell-compat
mkdir -p /mnt/vendor/persist /boot/EFI/arch /boot/grub2
shopt -s nullglob
for uki in /boot/efi/EFI/arch/*.efi; do cp "$uki" /boot/EFI/arch/; done
test -s /boot/EFI/arch/arch-linux-nabu.efi
python3 - <<'PY'
import sys, subprocess, pwd, grp, os, json
from pathlib import Path
sys.path.insert(0, '/build/scripts')
from nabu_layout import load_layout
from nabu_provision import load_provision
from nabu_grub import menu_config
from nabu_image_options import load_options, kernel_cmdline
from nabu_kernel import select as select_kernel, commandline as kernel_commandline
from nabu_filesystems import fstab
d=load_layout()
manager=os.environ.get('NABU_BOOT_MANAGER','grub')
if manager not in ('grub','limine'):
    raise SystemExit('Unsupported Nabu boot manager')
if manager == 'limine':
    if d.get('boot_filesystem_type') != 'vfat':
        raise SystemExit('Snapshot boot requires an explicit FAT boot layout')
    from nabu_limine import install_snapshot_initramfs
    install_snapshot_initramfs(Path('/'))
    hook=Path('/etc/pacman.d/hooks/90-linux-nabu.uki.hook')
    hook.unlink(missing_ok=True)
    hook.symlink_to('/dev/null')
else:
    hook=Path('/etc/pacman.d/hooks/90-linux-nabu.uki.hook')
    hook.unlink(missing_ok=True)
    hook.write_bytes(Path('/build/upstream/nabu/base/overlay/etc/pacman.d/hooks/90-linux-nabu.uki.hook').read_bytes())
p=load_provision()
name=p['username']
try:
    existing=pwd.getpwnam(name)
    if existing.pw_uid != 1000:
        raise SystemExit('Provisioning username conflicts with a system account')
except KeyError:
    subprocess.run(['usermod', '-l', name, '-d', '/home/'+name, '-m', 'tablet'], check=True)
if name != 'tablet':
    try:
        grp.getgrnam('tablet')
        subprocess.run(['groupmod', '-n', name, 'tablet'], check=True)
    except KeyError:
        pass
subprocess.run(['chpasswd', '-e'], input=name+':'+p['password_hash']+'\n', text=True, check=True)
home=Path('/home')/name
mimeapps=home/'.config/mimeapps.list'
if not mimeapps.exists():
    mimeapps.parent.mkdir(parents=True, exist_ok=True)
    mimeapps.write_text(Path('/build/overlay/home/tablet/.config/mimeapps.list').read_text())
ssh=home/'.ssh'
ssh.mkdir(mode=0o700, exist_ok=True)
authorized=ssh/'authorized_keys'
if p['authorized_keys']:
    authorized.write_text('\n'.join(p['authorized_keys'])+'\n')
    authorized.chmod(0o600)
else:
    authorized.unlink(missing_ok=True)
subprocess.run(['chown', '-R', name+':'+name, str(home)], check=True)
Path('/etc/sddm.conf.d/90-nabu.conf').write_text(
    '[Autologin]\nUser='+name+'\nSession=omarchy\n' if p['autologin'] else '[Users]\nRememberLastUser=false\n')
Path('/etc/ssh/sshd_config.d').mkdir(parents=True, exist_ok=True)
Path('/etc/ssh/sshd_config.d/90-nabu-key-only.conf').write_text(
    'PermitRootLogin no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n')
# Arch's sshdgenkeys.service generates device-unique keys at first boot.
for key in Path('/etc/ssh').glob('ssh_host_*'):
    key.unlink()
for key in Path('/root/.ssh').glob('*'):
    if key.is_file() or key.is_symlink():
        key.unlink()
subprocess.run(['systemctl', 'enable' if p['authorized_keys'] else 'disable', 'sshd'], check=True)
Path('/etc/omarchy-nabu').mkdir(exist_ok=True)
Path('/etc/omarchy-nabu/install.json').write_text(json.dumps({
    'username':name, 'autologin':p['autologin'], 'ssh_enabled':bool(p['authorized_keys']),
    'boot_profile':'existing-uboot', 'project':'Omarchy Quattro on Xiaomi Nabu'}, indent=2)+'\n')
Path('/etc/fstab').write_text(fstab(d))
cmdline=kernel_cmdline(Path('/build/profiles/nabu/kernel.cmdline').read_text(), load_options())
Path('/etc/cmdline.d/root.conf').write_text(cmdline)
selection=select_kernel()
if manager == 'limine':
    from nabu_limine import runtime_config
    Path('/etc/default').mkdir(parents=True,exist_ok=True)
    Path('/etc/default/limine').write_text(runtime_config(selection['slot'],kernel_commandline(cmdline,selection)))
    subprocess.run(['install','-Dm755','/build/scripts/omarchy-nabu-snapshot-init',
                    '/usr/local/libexec/omarchy-nabu-snapshot-init'],check=True)
    subprocess.run(['install','-Dm644','/build/profiles/nabu/omarchy-nabu-snapshot-init.service',
                    '/etc/systemd/system/omarchy-nabu-snapshot-init.service'],check=True)
    for unit in ('systemd-remount-fs.service', 'systemd-growfs-root.service'):
        subprocess.run(['install', '-Dm644', '/build/profiles/nabu/require-btrfs-root.conf',
                        f'/etc/systemd/system/{unit}.d/10-nabu-btrfs-root.conf'], check=True)
    drop=Path('/etc/systemd/system/limine-snapper-sync.service.d')
    drop.mkdir(parents=True,exist_ok=True)
    (drop/'10-nabu-init.conf').write_text('[Unit]\nRequires=omarchy-nabu-snapshot-init.service\nAfter=omarchy-nabu-snapshot-init.service\n')
    subprocess.run(['systemctl','enable','omarchy-nabu-snapshot-init.service',
                    'limine-snapper-sync.service','snapper-cleanup.timer'],check=True)
    subprocess.run(['systemctl','disable','snapper-timeline.timer'],check=True)
    Path('/var/lib/omarchy-nabu/snapshot-ready').unlink(missing_ok=True)

Path('/etc/omarchy-nabu/kernel.json').write_text(json.dumps(selection, indent=2)+'\n')
Path('/etc/kernel/cmdline-nabu-'+selection['slot']).write_text(kernel_commandline(cmdline, selection))
for p in Path('/etc/mkinitcpio.d').glob('linux-nabu*.preset'):
 preset=p.read_text().replace('/boot/efi/EFI/arch/', '/boot/EFI/arch/')
 # Older local debug packages forced -c, suppressing Nabu's conf.d baseline.
 preset=preset.replace('ALL_config="/etc/mkinitcpio.conf"\n','')
 p.write_text(preset)
if manager == 'grub':
    grub=menu_config(d,json.loads(Path('/build/profiles/nabu/grub.json').read_text()),selection['slot'])
    Path('/boot/grub2/grub.cfg').write_text(grub)
PY
systemctl enable systemd-networkd omarchy-nabu-usb-gadget serial-getty@ttyGS0.service
if [[ ${NABU_BOOT_MANAGER:-grub} == limine ]]; then
  # The stock UKI is only bootstrap input; the Limine image retains one tested kernel.
  test -s /boot/EFI/arch/arch-linux-nabu.efi
else
  /usr/libexec/nabu/uki-regenerate 2>&1 | tee "$output/initramfs-stock.log"
fi
cp /boot/EFI/arch/arch-linux-nabu.efi /boot/EFI/arch/arch-linux-nabu-stock.efi
rm -f /boot/EFI/arch/arch-linux-nabu-old.efi
# The existing FAT ESP is retained on the tablet. Its contents are not replaced.
rm -rf /boot/efi/EFI
: > /etc/machine-id
pacman -Q > "$output/packages.lock"
