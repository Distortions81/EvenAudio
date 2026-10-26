#!/usr/bin/env python3
"""EvenAudio: seven-band live desktop audio compression for Linux."""
import argparse
import multiprocessing as mp
import queue
import signal
import sys
import time

from engine import Meters, audio_worker
from routing import Routing, listing, recover_stale_routes
from presets import PRESETS, get_preset


class Session:
    def __init__(self):
        self.route = self.process = None
        self.ready = False
        self.started = 0

    def start(self, params, output=None):
        if self.route:
            raise RuntimeError('Already running')
        ctx = mp.get_context('spawn')
        self.controls, self.messages = ctx.Queue(4), ctx.Queue(32)
        self.stop_event = ctx.Event()
        try:
            recover_stale_routes(output)
            self.route = Routing(output)
            self.route.create()
            self.process = ctx.Process(target=audio_worker,
                args=(self.route.name + '.monitor', self.route.output, self.controls,
                      self.messages, self.stop_event, bytes(params)), daemon=True)
            self.process.start()
            self.route.worker_pid = self.process.pid
            self.started = time.monotonic()
        except Exception:
            self.stop()
            raise

    def update(self, params):
        try:
            self.controls.put_nowait(bytes(params))
        except queue.Full:
            pass

    def poll(self):
        events = []
        if not self.process:
            return events
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'ready':
                try:
                    self.route.activate()
                    self.ready = True
                except Exception as exc:
                    events.append(('error', str(exc)))
            elif kind == 'meters':
                value = Meters.from_buffer_copy(value)
            events.append((kind, value))
        if not self.process.is_alive() and not any(k == 'error' for k, _ in events):
            events.append(('error', 'Audio worker stopped unexpectedly'))
        if not self.ready and time.monotonic() - self.started > 10:
            events.append(('error', 'Timed out connecting to the audio server'))
        return events

    def stop(self):
        errors = []
        # Restore playback before stopping the worker so streams do not go silent.
        if self.route:
            errors = self.route.close()
        if self.process:
            self.stop_event.set()
            if self.process.pid is not None:
                self.process.join(timeout=.7)
                if self.process.is_alive():
                    self.process.terminate()
                    self.process.join(timeout=1)
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join(timeout=1)
            self.process = None
        if hasattr(self, 'controls'):
            for q in (self.controls, self.messages):
                q.cancel_join_thread()
                q.close()
            del self.controls
        self.route = None
        self.ready = False
        return errors


def meter_text(m):
    warning = '  OVER 0 dBFS — lower output gain' if m.output_db >= 0 else ''
    return (f'In {m.input_db:5.1f} dBFS    Out {m.output_db:5.1f} dBFS    '
            f'Voice gain {m.auto_db:+5.1f} dB{warning}')


def gui(initial, output, preset_key='general'):
    from interface import run_gui
    run_gui(initial, output, Session, meter_text, preset_key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headless', action='store_true', help='Run without a GUI; Ctrl+C restores playback')
    parser.add_argument('--list-outputs', action='store_true')
    parser.add_argument('--output', help='Output sink name (default: current default sink)')
    parser.add_argument('--preset', choices=PRESETS, default='general',
                        help='Starting settings (default: general)')
    parser.add_argument('--ratio', type=float, default=None,
                        help='Shared compression ratio for all bands, 1…1000 (general preset: 4)')
    parser.add_argument('--release', type=float, default=None,
                        help='Override all band releases in ms, 10…5000 '
                             '(otherwise uses the selected preset curve)')
    parser.add_argument('--threshold', type=float, default=None, help='Override all-band threshold dBFS, -60…0')
    parser.add_argument('--gain', type=float, default=None, help='Override output gain dB, -24…18')
    leveling = parser.add_mutually_exclusive_group()
    leveling.add_argument('--auto-level', action='store_true', dest='auto_level',
                          help='Level overall volume for uneven voices in podcasts/calls; can affect music')
    leveling.add_argument('--no-auto-level', action='store_false', dest='auto_level',
                          help='Disable overall voice leveling (off in music presets)')
    parser.set_defaults(auto_level=None)
    args = parser.parse_args()
    for name, lo, hi in (('ratio', 1, 1000), ('release', 10, 5000),
                         ('threshold', -60, 0), ('gain', -24, 18)):
        value = getattr(args, name)
        if value is not None and not lo <= value <= hi:
            parser.error(f'--{name} must be between {lo} and {hi}')
    def terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    session = Session()
    try:
        if args.list_outputs:
            for s in listing('sinks'):
                print(s['name'] + '\t' + s.get('description', ''))
            return 0
        p = get_preset(args.preset)
        if args.ratio is not None:
            p.ratio = args.ratio
        for b in range(7):
            if args.threshold is not None:
                p.threshold[b] = args.threshold
            if args.release is not None:
                p.release_ms[b] = args.release
        if args.gain is not None:
            p.output_db = args.gain
        if args.auto_level is not None:
            p.auto_level = float(args.auto_level)
        if not args.headless:
            gui(p, args.output, args.preset)
            return 0
        session.start(p, args.output)
        print('Connecting… Ctrl+C to stop and restore playback.', flush=True)
        while True:
            for kind, value in session.poll():
                if kind == 'error':
                    raise RuntimeError(value)
                if kind == 'ready':
                    print('Running: 7 bands, instant attack, no separate limiter.', flush=True)
                if kind == 'meters':
                    print('\r' + meter_text(value).ljust(110), end='', flush=True)
            time.sleep(.1)
    except KeyboardInterrupt:
        print('\nStopped.')
        return 0
    except Exception as exc:
        print(f'EvenAudio: {exc}', file=sys.stderr)
        return 1
    finally:
        for error in session.stop():
            print('Cleanup: ' + error, file=sys.stderr)


if __name__ == '__main__':
    sys.exit(main())
