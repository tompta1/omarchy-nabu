"""Render the Nabu UKI menu and an optional retained-loader recovery entry."""
from pathlib import PurePosixPath
import re
import uuid


def kernel_entries(primary='debug'):
    if primary not in ('debug', 'local'):
        raise ValueError('Unsupported primary Nabu kernel slot')
    return {'Omarchy Nabu' + (' debug' if primary == 'debug' else ''):
            'arch-linux-nabu-'+primary+'.efi',
            'Omarchy Nabu stock fallback': 'arch-linux-nabu-stock.efi'}


def menu_config(layout, profile, primary='debug'):
    boot = str(uuid.UUID(layout['boot_filesystem_uuid']))
    menu = 'set timeout=10\nset default=0\n'
    for label, filename in kernel_entries(primary).items():
        menu += f'''menuentry '{label}' {{
  search --no-floppy --fs-uuid --set=root {boot}
  chainloader /EFI/arch/{filename}
}}
'''
    recovery = profile.get('recovery_loader')
    if recovery:
        version = profile['recovery_version']
        label = profile.get('recovery_label', 'Original GRUB '+version+' recovery')
        esp = layout['esp_filesystem_uuid']
        if (not re.fullmatch(r'/EFI/[A-Za-z0-9_./-]+\.efi', recovery)
                or '..' in PurePosixPath(recovery).parts
                or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', version)
                or not re.fullmatch(r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}', esp)
                or not re.fullmatch(r'[A-Za-z0-9 ._-]+', label)):
            raise ValueError('Invalid retained-loader recovery mapping')
        menu += f'''search --no-floppy --fs-uuid --set=nabu_esp {esp}
if [ -f ($nabu_esp){recovery} ]; then
  menuentry '{label}' {{
    search --no-floppy --fs-uuid --set=root {esp}
    chainloader {recovery}
  }}
fi
'''
    return menu


def candidate_menu(current, boot_uuid):
    """Append one inactive kernel test; consume its request before selecting it."""
    boot = str(uuid.UUID(boot_uuid))
    if current.count('set default=0\n') != 1 or 'nabu-candidate' in current or 'next_entry' in current:
        raise ValueError('Unsupported or already modified candidate menu')
    once = '''if [ -f /grub2/nabu-candidate.env ]; then
  load_env --file /grub2/nabu-candidate.env nabu_once
  if [ "${nabu_once}" = "candidate" ]; then
    set nabu_once=
    if save_env --file /grub2/nabu-candidate.env nabu_once; then
      set default=nabu-candidate
    fi
  fi
fi
'''
    once = f'search --no-floppy --fs-uuid --set=root {boot}\n'+once
    result = current.replace('set default=0\n', 'set default=0\n'+once, 1)
    return result+f'''
menuentry 'Omarchy Nabu kernel candidate' --id 'nabu-candidate' {{
  search --no-floppy --fs-uuid --set=root {boot}
  chainloader /EFI/arch/arch-linux-nabu-debug-next.efi
}}
'''


def limine_candidate_menu(current, boot_uuid, esp_uuid):
    """Add an independent one-shot EFI handoff test to an existing menu."""
    boot = str(uuid.UUID(boot_uuid))
    if not re.fullmatch(r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}', esp_uuid):
        raise ValueError('Invalid ESP identity')
    if current.count('set default=0\n') != 1 or 'nabu-limine' in current:
        raise ValueError('Unsupported or already modified Limine menu')
    first = current.find('menuentry ')
    if first < 0:
        raise ValueError('No retained kernel menu')
    once = f'''search --no-floppy --fs-uuid --set=root {boot}
if [ -f /grub2/nabu-limine.env ]; then
  load_env --file /grub2/nabu-limine.env limine_once
  if [ "${{limine_once}}" = "candidate" ]; then
    set limine_once=
    if save_env --file /grub2/nabu-limine.env limine_once; then
      set default=nabu-limine
    fi
  fi
fi
'''
    return current[:first]+once+current[first:]+f'''
menuentry 'Omarchy Nabu Limine handoff test' --id 'nabu-limine' {{
  search --no-floppy --fs-uuid --set=root {esp_uuid}
  chainloader /EFI/omarchy-nabu/limine/BOOTAA64.EFI
}}
'''
