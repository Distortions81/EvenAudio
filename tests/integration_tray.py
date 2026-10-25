"""Real AppIndicator/DBusMenu check against an isolated tray watcher.

Run: dbus-run-session -- xvfb-run -a python3 tests/integration_tray.py
Needs the optional tray packages. Does not use the desktop session bus.
"""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tray import Tray


def main():
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    name = 'org.kde.StatusNotifierWatcher'
    interface = Gio.DBusNodeInfo.new_for_xml('''<node>
      <interface name="org.kde.StatusNotifierWatcher">
        <method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
        <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
        <property name="ProtocolVersion" type="i" access="read"/>
        <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
      </interface></node>''').interfaces[0]
    registered = []

    def method(connection, sender, path, iface, method, params, invocation):
        item = params.unpack()[0]
        registered.append((sender, item if item.startswith('/') else '/StatusNotifierItem'))
        invocation.return_value(None)

    def prop(connection, sender, path, iface, key):
        return {'IsStatusNotifierHostRegistered': GLib.Variant('b', True),
                'ProtocolVersion': GLib.Variant('i', 0),
                'RegisteredStatusNotifierItems': GLib.Variant('as', [])}[key]

    registration = bus.register_object('/StatusNotifierWatcher', interface, method, prop, None)

    def dbus(method, signature, values):
        return bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                             'org.freedesktop.DBus', method, GLib.Variant(signature, values),
                             None, Gio.DBusCallFlags.NONE, 2000, None)

    dbus('RequestName', '(su)', (name, 0))
    tray = Tray()
    received = []
    running, bypassed = False, False

    def wait(predicate, label):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            received.extend(tray.poll(running, bypassed))
            if predicate():
                return
            time.sleep(.02)
        raise AssertionError(label)

    try:
        wait(lambda: tray.connected and registered, 'Indicator did not connect to watcher')
        destination, path = registered[-1]

        def call(path, iface, method, signature, values):
            return bus.call_sync(destination, path, iface, method,
                                 GLib.Variant(signature, values), None,
                                 Gio.DBusCallFlags.NONE, 2000, None).unpack()

        menu_path = call(path, 'org.freedesktop.DBus.Properties', 'Get', '(ss)',
                         ('org.kde.StatusNotifierItem', 'Menu'))[0]

        def menu_items():
            layout = call(menu_path, 'com.canonical.dbusmenu', 'GetLayout', '(iias)',
                          (0, -1, ['label']))[1]
            return {child[1].get('label'): child[0] for child in layout[2]}

        wait(lambda: 'Start processing' in menu_items(), 'Initial menu missing')
        for label, action in (('Show EvenAudio', 'show'), ('Start processing', 'toggle'),
                              ('Enable bypass', 'bypass'), ('Quit', 'quit')):
            call(menu_path, 'com.canonical.dbusmenu', 'Event', '(isvu)',
                 (menu_items()[label], 'clicked', GLib.Variant('i', 0), 0))
            wait(lambda: action in received, 'Missing action ' + action)
        running, bypassed = True, True
        wait(lambda: 'Stop processing' in menu_items() and 'Disable bypass' in menu_items(),
             'Menu did not follow processing/bypass state')
        dbus('ReleaseName', '(s)', (name,))
        wait(lambda: not tray.connected, 'Watcher loss not detected')
        dbus('RequestName', '(su)', (name, 0))
        wait(lambda: tray.connected, 'Watcher reconnect not detected')
        tray.process.terminate()
        wait(lambda: not tray.connected, 'Helper death not detected')
        print('PASS: real indicator registration, all four menu actions, live labels, watcher loss/reconnect, helper death')
    finally:
        tray.close()
        bus.unregister_object(registration)
        dbus('ReleaseName', '(s)', (name,))


if __name__ == '__main__':
    main()
