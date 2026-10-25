#!/usr/bin/env python3
"""EvenAudio: seven-band live desktop audio compression for Linux."""
import argparse
import multiprocessing as mp
import queue
import signal
import sys
import time

from engine import BAND_NAMES, Meters, Params, audio_worker
from routing import Routing, listing, pactl


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
            f'Auto gain {m.auto_db:+5.1f} dB{warning}')


def gui(initial, output):
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except ImportError as exc:
        raise RuntimeError('GUI needs python3-tk (Debian/Ubuntu) or python3-tkinter '
                           '(Fedora). You can also run --headless.') from exc
    root = tk.Tk()
    root.title('EvenAudio · seven-band compressor')
    root.minsize(800, 560)
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill='both', expand=True)
    session = Session()
    ttk.Label(frame, text='EvenAudio', font=('', 24, 'bold')).grid(row=0, column=0, columnspan=4, sticky='w')
    ttk.Label(frame, text='Seven bands · instant attack · no separate limiter').grid(
        row=1, column=0, columnspan=4, sticky='w', pady=(0, 18))
    ttk.Label(frame, text='Play through').grid(row=2, column=0, sticky='w')
    output_var = tk.StringVar()
    selector = ttk.Combobox(frame, textvariable=output_var, state='readonly', width=62)
    selector.grid(row=2, column=1, columnspan=3, sticky='ew')
    sink_names = {}
    status = tk.StringVar(value='Stopped. Start to route desktop playback through EvenAudio.')
    meters = tk.StringVar(value='Input / output meters appear during playback.')

    def refresh():
        try:
            default = output or pactl('get-default-sink')
            sink_names.clear()
            for s in listing('sinks'):
                if not s['name'].startswith('evenaudio_'):
                    label = s.get('description', s['name']) + ' [' + s['name'] + ']'
                    sink_names[label] = s['name']
            selector['values'] = list(sink_names)
            output_var.set(next((k for k, v in sink_names.items() if v == default),
                                next(iter(sink_names), '')))
        except Exception as exc:
            status.set(str(exc))

    band_vars = []
    reduction_vars = []
    for c, title in enumerate(('Band', 'Threshold (dBFS)', 'Ratio (:1)', 'Release (ms)', 'Reduction')):
        ttk.Label(frame, text=title).grid(row=3, column=c, sticky='w', pady=(18, 7))
    for b, name in enumerate(BAND_NAMES):
        row = 4 + b
        ttk.Label(frame, text=name).grid(row=row, column=0, sticky='w', padx=(0, 15), pady=5)
        values = []
        for c, (value, lo, hi, step) in enumerate(((initial.threshold[b], -60, 0, 1),
            (initial.ratio[b], 1, 1000, 1), (initial.release_ms[b], 10, 5000, 10)), 1):
            var = tk.StringVar(value=f'{value:g}')
            ttk.Spinbox(frame, from_=lo, to=hi, increment=step, textvariable=var,
                        width=12).grid(row=row, column=c, sticky='ew', padx=(0, 14))
            values.append(var)
        band_vars.append(values)
        reduction = tk.StringVar(value='—')
        reduction_vars.append(reduction)
        ttk.Label(frame, textvariable=reduction, width=12).grid(row=row, column=4, sticky='w')

    auto = tk.BooleanVar(value=bool(initial.auto_level))
    bypass = tk.BooleanVar(value=False)
    ttk.Checkbutton(frame, text='Automatic speaker leveling', variable=auto).grid(
        row=11, column=0, columnspan=2, sticky='w', pady=(18, 6))
    ttk.Checkbutton(frame, text='Bypass (compare original)', variable=bypass).grid(
        row=11, column=2, columnspan=3, sticky='w')
    globals_vars = {}
    for row, (name, label, value, lo, hi, step) in enumerate((
        ('output_db', 'Output gain (dB)', initial.output_db, -24, 18, 1),
        ('target_db', 'Level target (RMS dBFS)', initial.target_db, -36, -10, 1),
        ('max_boost_db', 'Maximum auto boost (dB)', initial.max_boost_db, 0, 24, 1),
        ('level_ms', 'Leveling time (ms)', initial.level_ms, 100, 10000, 100)), 12):
        ttk.Label(frame, text=label).grid(row=row, column=0, columnspan=2, sticky='w', pady=4)
        var = tk.StringVar(value=f'{value:g}')
        globals_vars[name] = (var, lo, hi)
        ttk.Spinbox(frame, from_=lo, to=hi, increment=step, textvariable=var,
                    width=12).grid(row=row, column=2, sticky='ew', padx=(0, 14))
    ttk.Label(frame, textvariable=meters).grid(row=16, column=0, columnspan=5, sticky='w', pady=(18, 6))
    ttk.Label(frame, textvariable=status, wraplength=750).grid(row=17, column=0, columnspan=5, sticky='w')

    def params():
        import math
        p = Params.default()
        for b, values in enumerate(band_vars):
            for name, var, lo, hi in zip(('threshold', 'ratio', 'release_ms'), values,
                                        (-60, 1, 10), (0, 1000, 5000)):
                value = float(var.get())
                if not math.isfinite(value) or not lo <= value <= hi:
                    raise ValueError(f'{BAND_NAMES[b]} {name} must be {lo}…{hi}')
                getattr(p, name)[b] = value
        for name, (var, lo, hi) in globals_vars.items():
            value = float(var.get())
            if not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError(f'{name} must be {lo}…{hi}')
            setattr(p, name, value)
        p.auto_level, p.bypass = float(auto.get()), float(bypass.get())
        return p

    def stop():
        errors = session.stop()
        button['text'] = 'Start processing'
        selector['state'] = 'readonly'
        status.set('Stopped. Playback restored.' + (' ' + '; '.join(errors) if errors else ''))

    def toggle():
        if session.route:
            stop()
            return
        try:
            p = params()
            if output_var.get() not in sink_names:
                raise ValueError('Select an available output device')
            session.start(p, sink_names[output_var.get()])
            selector['state'] = 'disabled'
            button['text'] = 'Stop processing'
            status.set('Connecting audio…')
        except Exception as exc:
            stop()
            messagebox.showerror('EvenAudio', str(exc))

    button = ttk.Button(frame, text='Start processing', command=toggle)
    button.grid(row=18, column=0, columnspan=2, sticky='w', pady=(18, 0))
    ttk.Button(frame, text='Refresh outputs', command=refresh).grid(
        row=18, column=2, sticky='w', pady=(18, 0))

    def tick():
        if session.route:
            try:
                for kind, value in session.poll():
                    if kind == 'error':
                        stop()
                        status.set('Audio stopped: ' + value)
                        break
                    if kind == 'ready':
                        status.set('Running. Changes apply live. Reduce output gain if the meter exceeds 0 dBFS.')
                    if kind == 'meters':
                        meters.set(meter_text(value))
                        for var, reduction in zip(reduction_vars, value.reduction):
                            var.set(f'{reduction:.1f} dB')
                if session.route:
                    session.update(params())
            except ValueError as exc:
                status.set('Edit a valid value to apply: ' + str(exc))
            except Exception as exc:
                stop()
                status.set(str(exc))
        root.after(100, tick)

    def close():
        stop()
        root.destroy()

    refresh()
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(100, tick)
    try:
        root.mainloop()
    finally:
        session.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headless', action='store_true', help='Run without a GUI; Ctrl+C restores playback')
    parser.add_argument('--list-outputs', action='store_true')
    parser.add_argument('--output', help='Output sink name (default: current default sink)')
    parser.add_argument('--ratio', type=float, default=10, help='All-band ratio, 1…1000 (default 10)')
    parser.add_argument('--release', type=float, default=None,
                        help='Override all band releases in ms, 10…5000 '
                             '(default, bass to treble: 2000/1414/1000/707/500/354/250)')
    parser.add_argument('--threshold', type=float, default=-24, help='All-band threshold dBFS, -60…0')
    parser.add_argument('--gain', type=float, default=6, help='Output gain dB, -24…18')
    leveling = parser.add_mutually_exclusive_group()
    leveling.add_argument('--auto-level', action='store_true', dest='auto_level',
                          help='Enable broadband speaker leveling for uneven speech')
    leveling.add_argument('--no-auto-level', action='store_false', dest='auto_level',
                          help='Disable broadband leveling (default; preserves band independence)')
    parser.set_defaults(auto_level=False)
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
        p = Params.default()
        for b in range(7):
            p.ratio[b], p.threshold[b] = args.ratio, args.threshold
            if args.release is not None:
                p.release_ms[b] = args.release
        p.output_db, p.auto_level = args.gain, float(args.auto_level)
        if not args.headless:
            gui(p, args.output)
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
