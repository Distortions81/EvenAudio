"""Window lifecycle checks; run under xvfb-run to exercise real Tk."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from appearance import preference_path, save_theme, load_theme
from window_preferences import DEFAULTS, load_preferences, save_preferences


class PreferenceTests(unittest.TestCase):
    def test_defaults_roundtrip_and_bad_types(self):
        with tempfile.TemporaryDirectory() as config, patch.dict(os.environ, XDG_CONFIG_HOME=config):
            self.assertEqual(load_preferences(), DEFAULTS)
            self.assertTrue(save_preferences({'keep_running': False, 'close_notice_seen': True}))
            self.assertFalse(load_preferences()['keep_running'])
            save_theme('light')
            self.assertTrue(load_preferences()['close_notice_seen'])
            self.assertEqual(load_theme(), 'light')
            path = preference_path().with_name('window.json')
            for text in ('{broken', '[]', 'null', '{"keep_running": "false"}'):
                path.write_text(text)
                self.assertEqual(load_preferences(), DEFAULTS)

    def test_unwritable_preferences(self):
        with tempfile.TemporaryDirectory() as config:
            blocked = Path(config) / 'file'
            blocked.touch()
            with patch.dict(os.environ, XDG_CONFIG_HOME=str(blocked)):
                self.assertFalse(save_preferences(DEFAULTS))


@unittest.skipUnless(os.environ.get('DISPLAY'), 'Needs X display (use xvfb-run)')
class WindowTests(unittest.TestCase):
    def exercise(self, scenario):
        import tkinter as tk
        import interface
        from presets import get_preset
        real_tk = tk.Tk
        errors = []

        class Session:
            route = True
            stops = 0
            updates = 0

            def stop(self):
                self.route = None
                self.stops += 1
                return []

            def poll(self):
                return []

            def update(self, params):
                self.updates += 1
                self.bypassed = bool(params.bypass)
                self.params = params

        class Tray:
            connected = True
            closed = False
            actions = []

            def poll(self, *_):
                actions, self.actions = self.actions, []
                return actions

            def close(self):
                self.closed = True

        session, tray = Session(), Tray()

        def walk(widget):
            yield widget
            for child in widget.winfo_children():
                yield from walk(child)

        def create():
            root = real_tk()
            root.report_callback_exception = lambda _, value, __: errors.append(value)
            destroy = root.destroy
            destroyed = False

            def cleanup():
                nonlocal destroyed
                if not destroyed:
                    for callback in root.tk.splitlist(root.tk.call('after', 'info')):
                        root.after_cancel(callback)
                    destroy()
                    destroyed = True

            root.destroy = cleanup

            def check():
                try:
                    close = lambda: root.tk.call(root.protocol('WM_DELETE_WINDOW'))
                    widgets = list(walk(root))
                    setting = next(w for w in widgets if w.winfo_class() == 'TCheckbutton'
                                   and w.cget('text') == 'Keep running when window is closed')
                    quit_button = next(w for w in widgets if w.winfo_class() == 'TButton'
                                       and w.cget('text') == 'Quit')
                    scenario(root, close, setting, quit_button, session, tray)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    cleanup()

            root.after(150, check)
            return root

        with tempfile.TemporaryDirectory() as config, \
                patch.dict(os.environ, XDG_CONFIG_HOME=config), \
                patch.object(tk, 'Tk', create), \
                patch.object(interface, 'Tray', return_value=tray), \
                patch.object(interface, 'listing', return_value=[]), \
                patch.object(interface, 'pactl', return_value='speaker'), \
                patch.object(interface, 'recover_stale_routes', return_value=False), \
                patch('tkinter.messagebox.showinfo') as notice:
            interface.run_gui(get_preset('general'), None, lambda: session, lambda _: '')
        self.assertFalse(errors, errors)
        self.assertEqual(session.stops, 1)
        self.assertTrue(tray.closed)
        return notice

    def test_close_preserves_processing_and_tray_actions_restore_controls(self):
        def scenario(root, close, setting, quit_button, session, tray):
            self.assertTrue(root.getvar(setting.cget('variable')))
            close()
            self.assertEqual(root.state(), 'withdrawn')
            self.assertTrue(session.route)
            tray.actions = ['show', 'bypass']
            root.after(150, root.quit)
            root.mainloop()
            self.assertEqual(root.state(), 'normal')
            self.assertTrue(session.bypassed)
            self.assertGreater(session.updates, 1)
            close()
            tray.actions = ['toggle']
            root.after(150, root.quit)
            root.mainloop()
            self.assertFalse(session.route)
            # Stop uses the same GUI path as the button; compensate cleanup count.
            session.stops -= 1
        notice = self.exercise(scenario)
        notice.assert_called_once()

    def test_no_tray_minimizes_and_lost_tray_recovers(self):
        def scenario(root, close, setting, quit_button, session, tray):
            tray.connected = False
            with patch.object(root, 'iconify') as minimize:
                close()
            minimize.assert_called_once()
            self.assertTrue(session.route)
            tray.connected = True
            close()
            tray.connected = False
            root.after(150, root.quit)
            root.mainloop()
            self.assertEqual(root.state(), 'normal')
            self.assertTrue(session.route)
        self.exercise(scenario)

    def test_disabled_close_exits_and_saves_preference(self):
        def scenario(root, close, setting, quit_button, session, tray):
            setting.invoke()
            self.assertFalse(load_preferences()['keep_running'])
            close()
        self.exercise(scenario)

    def test_explicit_quit_and_tray_quit_exit(self):
        self.exercise(lambda root, close, setting, quit_button, session, tray: quit_button.invoke())

        def scenario(root, close, setting, quit_button, session, tray):
            close()
            tray.actions = ['quit']
            root.mainloop()
        self.exercise(scenario)

    def test_shared_ratio_live_edits_and_presets(self):
        from presets import PRESETS

        def scenario(root, close, setting, quit_button, session, tray):
            frame = root.winfo_children()[0]
            chooser = frame.nametowidget('preset')
            ratio = frame.nametowidget('ratio')
            self.assertFalse(any(w.winfo_name().startswith('ratio_')
                                 for w in frame.winfo_children()))
            self.assertEqual(float(ratio.get()), 4)

            def tick():
                root.after(130, root.quit)
                root.mainloop()

            def edit(value):
                root.setvar(ratio.cget('textvariable'), value)
                tick()

            edit('2.5')
            self.assertEqual(chooser.get(), 'Custom')
            self.assertEqual(session.params.ratio, 2.5)
            for invalid in ('', 'nan', 'inf', '0', '1001'):
                edit(invalid)
                self.assertEqual(session.params.ratio, 2.5)
            for recipe in PRESETS.values():
                chooser.set(recipe.label)
                chooser.event_generate('<<ComboboxSelected>>')
                tick()
                self.assertEqual(float(ratio.get()), recipe.ratio)
                self.assertEqual(session.params.ratio, recipe.ratio)
                self.assertEqual(chooser.get(), recipe.label)
                self.assertEqual(list(session.params.threshold), [recipe.threshold] * 7)
                self.assertEqual(list(session.params.release_ms), list(recipe.releases))

        self.exercise(scenario)
