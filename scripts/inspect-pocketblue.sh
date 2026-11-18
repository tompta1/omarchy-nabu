#!/bin/bash
# Read-only tablet inspection. Run over an authenticated SSH connection.
set -u
printf 'KERNEL\n'
uname -a
printf '\nCMDLINE\n'
cat /proc/cmdline
printf '\nOS\n'
cat /etc/os-release
printf '\nDEPLOYMENTS\n'
timeout -k 2 15 rpm-ostree status --json 2>&1
printf '\nROOT MOUNT\n'
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS,UUID /
printf '\nESP MOUNT\n'
findmnt -T /boot/efi -o TARGET,SOURCE,FSTYPE,OPTIONS,UUID
printf '\nBLOCK LAYOUT\n'
lsblk -o NAME,SIZE,FSTYPE,LABEL,PARTLABEL,MOUNTPOINTS
printf '\nSPACE\n'
df -h / /var /boot /boot/efi
printf '\nBOOT STATUS\n'
timeout -k 2 10 bootctl status --no-pager 2>&1
printf '\nFIRMWARE VENDOR\n'
for path in /sys/class/dmi/id/bios_vendor /sys/class/dmi/id/bios_version; do
  [[ ! -r $path ]] || cat "$path"
done
printf '\nUSB INTERFACES\n'
ip -br address
