"""Optional AppIndicator helper. GTK lives outside Tk's process/event loop."""
import importlib
import multiprocessing as mp
from pathlib import Path


def _run(connection):
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        for namespace in ('AyatanaAppIndicator3', 'AppIndicator3'):
            try:
                gi.require_version(namespace, '0.1')
                AppIndicator = importlib.import_module('gi.repository.' + namespace)
                break
            except (ValueError, ImportError):
                continue
        else:
            return
        from gi.repository import Gtk, GLib
        if not Gtk.init_check()[0]:
            return
        indicator = AppIndicator.Indicator.new(
            'evenaudio', str(Path(__file__).resolve().parent / 'assets/evenaudio.svg'),
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        indicator.set_title('EvenAudio')
        menu = Gtk.Menu()

        def send(action):
            try:
                connection.send(action)
            except (OSError, EOFError):
                Gtk.main_quit()

        def item(label, action):
            widget = Gtk.MenuItem(label=label)
            widget.connect('activate', lambda *_: send(action))
            menu.append(widget)
            return widget

        item('Show EvenAudio', 'show')
        toggle = item('Start processing', 'toggle')
        bypass = item('Enable bypass', 'bypass')
        menu.append(Gtk.SeparatorMenuItem())
        item('Quit', 'quit')
        menu.show_all()
        indicator.set_menu(menu)
        indicator.connect('connection-changed', lambda _, connected: send(bool(connected)))
        indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        send(bool(indicator.get_property('connected')))

        def tick():
            try:
                while connection.poll():
                    state = connection.recv()
                    if state is None:
                        Gtk.main_quit()
                        return False
                    running, bypassed = state
                    toggle.set_label('Stop processing' if running else 'Start processing')
                    bypass.set_label('Disable bypass' if bypassed else 'Enable bypass')
                    indicator.set_title('EvenAudio · ' + (
                        'Bypassed' if running and bypassed else 'Running' if running else 'Stopped'))
            except (EOFError, OSError):
                Gtk.main_quit()
                return False
            return True

        GLib.timeout_add(100, tick)
        Gtk.main()
        indicator.set_status(AppIndicator.IndicatorStatus.PASSIVE)
    except (ImportError, ValueError, RuntimeError):
        # Optional system packages/display/session bus may be unavailable.
        pass
    finally:
        connection.close()


class Tray:
    def __init__(self):
        self.connected = False
        self.last_state = None
        self.process = self.connection = None
        child = None
        try:
            ctx = mp.get_context('spawn')
            self.connection, child = ctx.Pipe()
            self.process = ctx.Process(target=_run, args=(child,), daemon=True)
            self.process.start()
        except (OSError, RuntimeError):
            self.close()
        finally:
            if child is not None:
                child.close()

    def poll(self, running, bypassed):
        actions = []
        try:
            if not self.process or not self.process.is_alive():
                self.connected = False
                return actions
            while self.connection.poll():
                event = self.connection.recv()
                if type(event) is bool:
                    self.connected = event
                elif event in ('show', 'toggle', 'bypass', 'quit'):
                    actions.append(event)
            state = bool(running), bool(bypassed)
            if state != self.last_state:
                self.connection.send(state)
                self.last_state = state
        except (EOFError, OSError):
            self.connected = False
        return actions

    def close(self):
        self.connected = False
        if self.connection:
            try:
                self.connection.send(None)
            except (OSError, EOFError):
                pass
            self.connection.close()
            self.connection = None
        if self.process and self.process.pid is not None:
            self.process.join(timeout=.5)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=.5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join()
        self.process = None
