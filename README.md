# Omarchy Quattro on Xiaomi Nabu

Omarchy Quattro 4.5 on Arch Linux ARM for the Xiaomi Pad 5 (`nabu`).
Packages use `pacman`. The tested stack boots an ARM64 Linux 7.4.0-rc3 UKI
through U-Boot and Limine, with Btrfs root, a 4 GiB FAT `/boot`, and a separate
FAT16 ESP at `/efi`.

## Build and install

### First-time partition setup

Use an unlocked Nabu with the supported U-Boot already installed. For a stock
GPT, enable U-Boot USB Mass Storage (UMS) and identify the tablet’s whole UMS
disk on the host. Unmount its partitions. Set `UMS_DISK` to that disk. Keep
the original GPT and a recovery copy of the ESP. Capture `ubootefi.var`
from the tablet's ESP for the build. Resizing userdata and installing Omarchy
will erase its contents.

```bash
mkdir -p local
export GPT_CAPTURE=local/original-gpt GPT_PROPOSAL=local/tail-boot-gpt
sudo python3 scripts/nabu_gpt_writer.py --device "$UMS_DISK" \
  --capture "$GPT_CAPTURE" --proposal "$GPT_PROPOSAL" --prepare
export NABU_LAYOUT="$GPT_PROPOSAL/layout.json"
```

On a previously migrated tablet, reuse the saved capture, proposal and layout.

### Image build

Build on x86-64 Linux with Podman, ARM64 binfmt/QEMU and at least 120 GiB
free. Keep device inputs under ignored `local/`. Save your provisioning as
`local/provision.json` using the [example](profiles/nabu/provision.example.json).
The default profile uses upstream Omarchy with board integration.
[Tablet UI options](profiles/nabu/image-options.example.json) are opt-in via
`NABU_IMAGE_OPTIONS`.

```bash
NABU_KERNEL_PROFILE=kernel/profiles/7.4-rc3-candidate.json ./scripts/build-debug-kernel.sh
./scripts/build-limine.sh
export BUNDLE=out/runs/limine
NABU_BOOT_MANAGER=limine NABU_PROVISION=local/provision.json \
  NABU_KERNEL_INPUT_REL=out/kernel-7.4-rc3-candidate \
  NABU_LIMINE_INPUT_REL=out/limine-12.10.0 NABU_OUTPUT_REL="$BUNDLE" \
  ./scripts/build-tablet-image.sh
python3 scripts/assemble-limine-esp.py --loader out/limine-12.10.0/BOOTAA64.EFI \
  --loader-only --firmware-vars local/ubootefi.var --output "$BUNDLE/esp-component"
```

Run ESP assembly with the native filesystem tools installed. Supply the
U-Boot EFI variables captured from the target. The
[manual Actions workflow](.github/workflows/images.yml) can build and verify
the stack on a self-hosted `nabu-builder` runner and discard test images.

### Fastboot install

For a first install, verify the finished bundle, then return to UMS and run
the same writer command with `--apply` in place of `--prepare`. Keep the capture
and the proposal with its verified write journal. Enter Xiaomi FASTBOOT
directly after the GPT write. Reinstalls use Fastboot alone.

1. Enter Xiaomi FASTBOOT with Power + Volume Down. Export `NABU_LAYOUT`
   and `BUNDLE` for the matching build, `GPT_CAPTURE` and `GPT_PROPOSAL`
   for the original migration, and `SERIAL` for the tablet's Fastboot serial.
2. Run the read-only check. After it passes, run the flash command.
   **Flashing erases userdata.** Leave USB connected until all writes finish.

```bash
python3 scripts/nabu_limine_flash.py --output "$BUNDLE" --capture "$GPT_CAPTURE" --proposal "$GPT_PROPOSAL" --serial "$SERIAL"
python3 scripts/nabu_limine_flash.py --output "$BUNDLE" --capture "$GPT_CAPTURE" --proposal "$GPT_PROPOSAL" --serial "$SERIAL" --flash --reboot
```

## Recovery

For Omarchy recovery, use a verified bundle for the same layout. Pocketblue
provides a separate Fastboot recovery route. Check image sizes against the
current layout, especially the reduced `userdata` partition, before using its
installer. Returning to Android may require the original GPT and matching
stock ROM. [Pocketblue Nabu recovery](https://pocketblue.github.io/devices/xiaomi-nabu/)

## Hardware

| Verified | Open or unqualified |
| --- | --- |
| Fresh install boots to the desktop, Wi-Fi connects, FD640 hardware rendering works | Physical snapshot restore and repeated suspend on the fresh install |
| Both cameras deliver viewfinder frames, protected speaker routing is active | Intermittent rear lens initialization timeout, audible stereo and microphone quality on the fresh install |
| Hardware H.264 and HEVC decode at playback speed | Hardware VP9 fails, and a short unpaced H.264 clip intermittently stalls |
| Snapper snapshot boot and return to normal Btrfs passed on the fresh install. Touch, rotation, on-screen keyboard, Bluetooth typing, auto-brightness and unplugged Power-button wake passed on the earlier installation | Repeated peripheral checks, pen and USB OTG |

See [NOTICE](NOTICE) for source licensing and firmware distribution terms.
