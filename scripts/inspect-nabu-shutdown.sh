#!/bin/bash
# HW-14: read-only teardown evidence; redirect output to ignored lab storage.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root to read boot logs and unit details.' >&2; exit 1; }
grep -aq 'xiaomi,nabu' /proc/device-tree/compatible || { echo 'Not Xiaomi Nabu.' >&2; exit 1; }
uname -r
cat /proc/sys/kernel/random/boot_id
cat /sys/class/dmi/id/bios_version
cat /proc/uptime
cat /sys/power/mem_sleep
for file in /sys/power/suspend_stats/*; do
  [[ -r $file && -f $file ]] || continue
  printf '%s: ' "${file##*/}";cat "$file"
done
systemctl --failed --no-legend
for unit in hexagonrpcd.service iio-sensor-proxy.service rmtfs.service \
    tqftpserv.service qrtr-ns.service NetworkManager.service \
    omarchy-nabu-wifi-shutdown.service ath10k-shutdown.service; do
  echo "Unit: $unit"
  systemctl show "$unit" -p LoadState -p ActiveState -p SubState -p Result \
    -p After -p Before -p RequiresMountsFor -p ExecStart -p ExecStop
  if systemctl cat "$unit" >/dev/null 2>&1; then systemctl cat "$unit"; fi
done
findmnt -o TARGET,SOURCE,FSTYPE /mnt/vendor/persist || true
python3 - <<'PY'
from pathlib import Path
base=Path('/lib/firmware/hexagonfs')
print('Hexagon firmware directory:',base.resolve())
links=[p for p in base.rglob('*') if p.is_symlink()]
for p in links[:40]: print(p,'->',p.readlink())
PY
for remote in /sys/class/remoteproc/remoteproc*; do
  [[ -d $remote ]] || continue
  echo "Remote processor: ${remote##*/}"
  cat "$remote/name" "$remote/state"
done
if [[ -r /proc/config.gz ]]; then
  zcat /proc/config.gz | grep -E 'CONFIG_(ATH10K|QCOM_Q6V5|QCOM_FASTRPC|PREEMPT|RCU|CPU_IDLE|ARM_PSCI)' || true
fi
lsmod | grep -E 'ath10k|fastrpc|q6v5|remoteproc' || true
journalctl -k -b -p warning --no-pager -o short-monotonic -n 120
journalctl -k -b --no-pager -o short-monotonic | awk '/PM: suspend/'
