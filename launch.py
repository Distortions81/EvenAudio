#!/usr/bin/env python3
"""Desktop entry point: build locally when needed, then open EvenAudio."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def error(message):
    print('EvenAudio: ' + message, file=sys.stderr)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('EvenAudio', message, parent=root)
        root.destroy()
    except Exception:
        if shutil.which('zenity'):
            subprocess.run(['zenity', '--error', '--title=EvenAudio', '--text=' + message])
        elif shutil.which('xmessage'):
            subprocess.run(['xmessage', '-center', message])
    return 1


def main():
    directory = Path(__file__).resolve().parent
    try:
        import tkinter  # Check before attempting to build.
    except ImportError:
        return error('Install the GUI dependency, then launch again:\n'
                     'Debian/Ubuntu: sudo apt install python3-tk\n'
                     'Fedora: sudo dnf install python3-tkinter')
    try:
        result = subprocess.run(['make', '-C', str(directory)], capture_output=True, text=True)
    except FileNotFoundError:
        return error('Build tools are missing. On Debian/Ubuntu:\n'
                     'sudo apt install build-essential')
    if result.returncode:
        return error('Could not build the audio engine:\n' + (result.stderr or result.stdout)[-3000:])
    os.execv(sys.executable, [sys.executable, str(directory / 'evenaudio.py')])


if __name__ == '__main__':
    sys.exit(main())
