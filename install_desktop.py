#!/usr/bin/env python3
"""Install an application-menu entry and optional desktop shortcut; no sudo."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def exec_quote(value):
    # Exec field quoting (desktop-entry escaping is a separate outer layer).
    value = str(value).replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, '\\' + character)
    return '"' + value.replace('\\', '\\\\') + '"'


def desktop_directory():
    if shutil.which('xdg-user-dir'):
        result = subprocess.run(['xdg-user-dir', 'DESKTOP'], capture_output=True, text=True)
        candidate = Path(result.stdout.strip())
        if result.returncode == 0 and candidate.is_absolute() and candidate != Path.home():
            return candidate
    return Path.home() / 'Desktop'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', action='store_true', help='Also add an icon on your desktop')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    if not data.is_absolute():
        parser.error('XDG_DATA_HOME must be an absolute path')
    applications = data / 'applications'
    icons = data / 'icons/hicolor/scalable/apps'
    applications.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(directory / 'assets/evenaudio.svg', icons / 'evenaudio.svg')
    contents = '\n'.join([
        '[Desktop Entry]', 'Version=1.0', 'Type=Application', 'Name=EvenAudio',
        'Comment=Seven-band desktop audio compressor',
        'Exec=' + exec_quote(sys.executable) + ' ' + exec_quote(directory / 'launch.py'),
        'Icon=evenaudio', 'Terminal=false', 'Categories=AudioVideo;Audio;Mixer;',
        'Keywords=audio;compressor;multiband;volume;', 'StartupNotify=false', ''])
    launcher = applications / 'evenaudio.desktop'
    launcher.write_text(contents, encoding='utf-8')
    launcher.chmod(0o755)
    print('Installed application launcher: ' + str(launcher))
    if args.desktop:
        desktop = desktop_directory()
        desktop.mkdir(parents=True, exist_ok=True)
        shortcut = desktop / 'evenaudio.desktop'
        shutil.copyfile(launcher, shortcut)
        shortcut.chmod(0o755)
        if shutil.which('gio'):
            subprocess.run(['gio', 'set', str(shortcut), 'metadata::trusted', 'true'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print('Installed desktop shortcut: ' + str(shortcut))
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', str(applications)], check=False)
    print('Launch EvenAudio from your application menu. Some desktops require '
          'right-clicking the desktop icon and selecting Allow Launching.')


if __name__ == '__main__':
    main()
