"""Optional real-Tk smoke check, called inside the isolated integration server."""
from pathlib import Path
import subprocess
from unittest.mock import patch


def check_gui(original):
    import tkinter as tk
    from evenaudio import gui
    from engine import Params
    from routing import pactl
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

        def inspect():
            try:
                button('Stop processing')
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
            except Exception as exc:
                errors.append(exc)
            finally:
                root.destroy()

        root.after(100, start)
        root.after(2200, inspect)
        root.after(6000, root.destroy)
        return root

    with patch.object(tk, 'Tk', create):
        gui(Params.default(), None)
    assert not errors, errors
