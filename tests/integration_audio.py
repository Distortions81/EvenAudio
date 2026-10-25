"""Real server test in an isolated PulseAudio instance; never uses desktop audio.

Run: python3 tests/integration_audio.py
Needs pulseaudio, pactl, and permission to create local Unix sockets.
"""
import ctypes as C
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evenaudio import Session
from engine import FRAMES, Meters, Params, Pulse
from routing import listing, pactl


def main():
    with tempfile.TemporaryDirectory(prefix='evenaudio-test-') as temp:
        socket = temp + '/pulse'
        runtime = temp + '/runtime'
        os.mkdir(runtime, 0o700)
        old_env = {k: os.environ.get(k) for k in ('PULSE_SERVER', 'PULSE_RUNTIME_PATH')}
        os.environ.update(PULSE_SERVER='unix:' + socket, PULSE_RUNTIME_PATH=runtime)
        log = open(temp + '/server.log', 'w+')
        server = subprocess.Popen(['pulseaudio', '-n', '--daemonize=no', '--use-pid-file=no',
            '--exit-idle-time=-1', '--log-target=stderr',
            '--load=module-native-protocol-unix socket=' + socket + ' auth-anonymous=1',
            '--load=module-null-sink sink_name=ea_test_output rate=48000'],
            stdout=log, stderr=log)
        session = Session()
        player = None
        try:
            for _ in range(50):
                if Path(socket).exists():
                    break
                if server.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read())
                time.sleep(.1)
            original = pactl('get-default-sink')
            session.start(Params.default())
            for _ in range(100):
                for kind, value in session.poll():
                    if kind == 'error':
                        raise RuntimeError(value)
                if session.ready:
                    break
                time.sleep(.05)
            assert session.ready, 'Worker did not become ready'
            assert pactl('get-default-sink') == session.route.name
            virtual = session.route.name
            module = session.route.module
            player = Pulse(virtual, False)
            maximum = -240
            reductions = []
            for block in range(100):
                buffer = (C.c_float * (FRAMES * 2))()
                for i in range(FRAMES):
                    value = .6 * math.sin(2*math.pi*900*(block*FRAMES+i)/48000)
                    buffer[2*i] = buffer[2*i+1] = value
                player.transfer(buffer)
                for kind, value in session.poll():
                    if kind == 'error':
                        raise RuntimeError(value)
                    if kind == 'meters':
                        maximum = max(maximum, value.output_db)
                        reductions.append(max(value.reduction))
            assert maximum > -60, 'No processed signal reached playback'
            assert max(reductions, default=0) > 3, 'Expected compression on loud test tone'
            # Find the worker playback stream and verify explicit physical routing.
            streams = listing('sink-inputs')
            physical = next(s['index'] for s in listing('sinks') if s['name'] == original)
            assert any(s.get('properties', {}).get('media.name') == 'EvenAudio output'
                       and s['sink'] == physical for s in streams), streams
            errors = session.stop()
            assert not errors, errors
            assert pactl('get-default-sink') == original
            assert all(s['name'] != virtual for s in listing('sinks'))
            assert all(line.split()[0] != module
                       for line in pactl('list', 'short', 'modules').splitlines())
            print(f'PASS: live capture → seven-band DSP → pinned output; peak {maximum:.1f} dBFS')
            print('PASS: default output restored; virtual sink and module removed')
            # Failed output connection must unwind the virtual sink as well.
            session.start(Params.default())
            pactl('unload-module', session.route.module)
            for _ in range(100):
                events = session.poll()
                if any(k == 'error' for k, _ in events):
                    break
                time.sleep(.05)
            else:
                raise AssertionError('Missing worker error after sink removal')
            session.stop()
            assert pactl('get-default-sink') == original
            print('PASS: worker failure detected and route restored')
            if '--gui' in sys.argv:
                from gui_smoke import check_gui
                check_gui(original)
        finally:
            session.stop()
            if player:
                player.close()
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            log.close()
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == '__main__':
    main()
