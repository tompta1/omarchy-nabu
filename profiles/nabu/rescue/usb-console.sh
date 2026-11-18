#!/bin/sh
# Deliberate, physical-USB root console for an explicitly selected rescue UKI.
set -eu
BB=/usr/bin/busybox
$BB mkdir -p /sys/kernel/config
$BB mount -t configfs configfs /sys/kernel/config 2>/dev/null || true
gadget=/sys/kernel/config/usb_gadget/nabu-rescue
$BB mkdir -p "$gadget"
cd "$gadget"
printf '0x1d6b\n' > idVendor
printf '0x0104\n' > idProduct
$BB mkdir -p strings/0x409 configs/c.1/strings/0x409 functions/acm.usb0
printf 'nabu-rescue\n' > strings/0x409/serialnumber
printf 'Omarchy Nabu rescue\n' > strings/0x409/product
printf 'Omarchy\n' > strings/0x409/manufacturer
printf 'USB rescue console\n' > configs/c.1/strings/0x409/configuration
$BB ln -s "$gadget/functions/acm.usb0" configs/c.1/acm.usb0
attempt=0
while [ "$attempt" -lt 60 ]; do
  for udc in /sys/class/udc/*; do
    if [ -e "$udc" ]; then
      printf '%s\n' "${udc##*/}" > UDC
      while [ ! -e /dev/ttyGS0 ]; do $BB sleep 1; done
      $BB stty -F /dev/ttyGS0 115200 sane
      printf '\nNabu USB rescue: root mounted read-only at /sysroot.\n' >/dev/ttyGS0
      exec "$BB" setsid -c "$BB" sh -i </dev/ttyGS0 >/dev/ttyGS0 2>&1
    fi
  done
  attempt=$((attempt+1))
  $BB sleep 1
done
printf 'No USB device controller available for rescue\n' >&2
exit 1
