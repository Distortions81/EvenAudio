"""Optional real-Tk smoke check, called inside the isolated integration server."""
from pathlib import Path
import os
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def check_gui(original):
    import tkinter as tk
    from evenaudio import gui
    from engine import Params
    from routing import pactl, listing
    from presets import PRESETS
    from appearance import PALETTES, load_theme
    real_tk = tk.Tk
    errors = []
    roots = []

    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from walk(child)

    def create():
        root = real_tk()
        roots.append(root)
        root.report_callback_exception = lambda kind, value, trace: errors.append(value)

        def button(text):
            return next(w for w in walk(root) if w.winfo_class() == 'TButton'
                        and w.cget('text') == text)

        def start():
            button('Start processing').invoke()

        def close_processing_window():
            try:
                assert pactl('get-default-sink').startswith('evenaudio_')
                root.tk.call(root.protocol('WM_DELETE_WINDOW'))
                assert root.state() == 'withdrawn'
                assert pactl('get-default-sink').startswith('evenaudio_')
                root.after(300, quit_processing)
            except Exception as exc:
                errors.append(exc)
                root.destroy()

        def quit_processing():
            try:
                assert pactl('get-default-sink').startswith('evenaudio_')
                button('Quit').invoke()
            except Exception as exc:
                errors.append(exc)
                root.destroy()

        def inspect():
            try:
                button('Stop processing')
                widgets = list(walk(root))
                chooser = next(w for w in widgets if w.winfo_name() == 'preset')
                voice = next(w for w in widgets if w.winfo_class() == 'TCheckbutton'
                             and str(w.cget('text')).startswith('Level uneven voices'))
                ratio = next(w for w in widgets if w.winfo_name() == 'ratio')
                assert float(ratio.get()) == 4
                for recipe in PRESETS.values():
                    chooser.set(recipe.label)
                    chooser.event_generate('<<ComboboxSelected>>')
                    assert float(ratio.get()) == recipe.ratio
                    for band, release in enumerate(recipe.releases):
                        spin = next(w for w in widgets if w.winfo_name() == f'release_{band}')
                        assert float(spin.get()) == release
                    assert bool(root.getvar(voice.cget('variable'))) == recipe.voices
                edited = next(w for w in widgets if w.winfo_name() == 'release_0')
                edited.delete(0, 'end')
                edited.insert(0, '1800')
                assert chooser.get() == 'Custom'
                chooser.set(PRESETS['general'].label)
                chooser.event_generate('<<ComboboxSelected>>')
                dark = next(w for w in widgets if w.winfo_class() == 'TCheckbutton'
                            and w.cget('text') == 'Dark mode')
                assert root.cget('background') == PALETTES['dark']['bg']
                dark.invoke()
                assert root.cget('background') == PALETTES['light']['bg']
                assert load_theme() == 'light'
                root.update_idletasks()
                light = Path(__file__).resolve().parents[1] / 'preview-light.png'
                subprocess.run(['import', '-window', str(root.winfo_id()), str(light)],
                               check=True, timeout=5)
                dark.invoke()
                assert load_theme() == 'dark'
                root.update_idletasks()
                labels = [str(root.getvar(w.cget('textvariable'))) for w in walk(root)
                          if w.winfo_class() == 'TLabel' and w.cget('textvariable')]
                assert any(value.startswith('Running.') for value in labels), labels
                screenshot = Path(__file__).resolve().parents[1] / 'preview.png'
                subprocess.run(['import', '-window', str(root.winfo_id()), str(screenshot)],
                               check=True, timeout=5)
                button('Stop processing').invoke()
                assert pactl('get-default-sink') == original
                button('Start processing')
                print('PASS: GUI starts audio, shows live state, and restores playback on Stop')
                print('PASS: four presets, Custom edits, voice explanation, dark/light switch, and saved theme')
                button('Start processing').invoke()
                root.after(900, close_processing_window)
            except Exception as exc:
                errors.append(exc)
                root.destroy()

        root.after(100, start)
        root.after(2200, inspect)
        root.after(6000, root.destroy)
        return root

    with tempfile.TemporaryDirectory() as config, \
            patch.dict(os.environ, {'XDG_CONFIG_HOME': config}), \
            patch('interface.Tray', return_value=SimpleNamespace(
                connected=True, poll=lambda *_: [], close=lambda: None)), \
            patch('tkinter.messagebox.showinfo'), \
            patch.object(tk, 'Tk', create):
        gui(Params.default(), None)
    assert not errors, errors
    assert pactl('get-default-sink') == original
    assert not any(s['name'].startswith('evenaudio_') for s in listing('sinks'))
    print('PASS: closing the GUI preserves live routing; Quit restores output and removes virtual sinks')
