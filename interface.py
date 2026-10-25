"""Live controls, presets, and appearance for EvenAudio."""
import math

from appearance import apply_theme, load_theme, save_theme
from engine import BAND_NAMES, Params
from presets import PRESETS, get_preset
from routing import listing, pactl, recover_stale_routes


def run_gui(initial, output, session_factory, format_meters, preset_key='general'):
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except ImportError as exc:
        raise RuntimeError('GUI needs python3-tk (Debian/Ubuntu) or python3-tkinter '
                           '(Fedora). You can also run --headless.') from exc
    root = tk.Tk()
    root.title('EvenAudio · seven-band compressor')
    root.minsize(920, 720)
    dark = tk.BooleanVar(value=load_theme() == 'dark')
    apply_theme(root, 'dark' if dark.get() else 'light')
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    for column in (1, 2, 3):
        frame.columnconfigure(column, weight=1)
    session = session_factory()

    def change_theme():
        theme = 'dark' if dark.get() else 'light'
        apply_theme(root, theme)
        if not save_theme(theme):
            status.set('Appearance changed for this session; could not save your preference.')

    ttk.Label(frame, text='EvenAudio', style='Title.TLabel').grid(
        row=0, column=0, columnspan=3, sticky='w')
    ttk.Checkbutton(frame, text='Dark mode', variable=dark, command=change_theme).grid(
        row=0, column=4, sticky='e')
    ttk.Label(frame, text='Seven bands · instant attack · no separate limiter',
              style='Muted.TLabel').grid(row=1, column=0, columnspan=5, sticky='w', pady=(0, 12))
    ttk.Label(frame, text='Play through').grid(row=2, column=0, sticky='w', padx=(0, 12))
    output_var = tk.StringVar()
    selector = ttk.Combobox(frame, textvariable=output_var, state='readonly', width=60)
    selector.grid(row=2, column=1, columnspan=4, sticky='ew', pady=3)
    sink_names = {}
    status = tk.StringVar(value='Stopped. Start to route desktop playback through EvenAudio.')
    meters = tk.StringVar(value='Input / output meters appear during playback.')

    def refresh():
        try:
            default = output or pactl('get-default-sink')
            sink_names.clear()
            for sink in listing('sinks'):
                if not sink['name'].startswith('evenaudio_'):
                    label = sink.get('description', sink['name']) + ' [' + sink['name'] + ']'
                    sink_names[label] = sink['name']
            selector['values'] = list(sink_names)
            output_var.set(next((k for k, v in sink_names.items() if v == default),
                                next(iter(sink_names), '')))
        except Exception as exc:
            status.set(str(exc))

    ttk.Label(frame, text='Preset').grid(row=3, column=0, sticky='w')
    selected_preset = tk.StringVar(value=PRESETS[preset_key].label)
    preset_selector = ttk.Combobox(frame, name='preset', textvariable=selected_preset,
                                  values=[p.label for p in PRESETS.values()], state='readonly')
    preset_selector.grid(row=3, column=1, columnspan=4, sticky='ew', pady=3)
    preset_description = tk.StringVar(value=PRESETS[preset_key].description)
    ttk.Label(frame, textvariable=preset_description, style='Muted.TLabel', wraplength=860).grid(
        row=4, column=0, columnspan=5, sticky='w', pady=(4, 0))

    band_vars, reduction_vars = [], []
    for column, title in enumerate(('Band', 'Threshold (dBFS)', 'Ratio (:1)', 'Release (ms)', 'Reduction')):
        ttk.Label(frame, text=title).grid(row=5, column=column, sticky='w', pady=(14, 5))
    for band, name in enumerate(BAND_NAMES):
        row = 6 + band
        ttk.Label(frame, text=name).grid(row=row, column=0, sticky='w', padx=(0, 15), pady=3)
        values = []
        for column, (field, value, low, high, step) in enumerate((
            ('threshold', initial.threshold[band], -60, 0, 1),
            ('ratio', initial.ratio[band], 1, 1000, 1),
            ('release', initial.release_ms[band], 10, 5000, 10)), 1):
            var = tk.StringVar(value=f'{value:g}')
            ttk.Spinbox(frame, name=f'{field}_{band}', from_=low, to=high, increment=step,
                        textvariable=var, width=12).grid(row=row, column=column,
                                                       sticky='ew', padx=(0, 14))
            values.append(var)
        band_vars.append(values)
        reduction = tk.StringVar(value='—')
        reduction_vars.append(reduction)
        ttk.Label(frame, textvariable=reduction, width=12).grid(row=row, column=4, sticky='w')

    auto = tk.BooleanVar(value=bool(initial.auto_level))
    bypass = tk.BooleanVar(value=False)
    ttk.Checkbutton(frame, text='Level uneven voices (podcasts/calls)', variable=auto).grid(
        row=13, column=0, columnspan=3, sticky='w', pady=(10, 0))
    ttk.Checkbutton(frame, text='Bypass (compare original)', variable=bypass).grid(
        row=13, column=3, columnspan=2, sticky='w', pady=(10, 0))
    ttk.Label(frame, style='Muted.TLabel', wraplength=860, text=(
        'Raises quiet passages and lowers loud ones using overall volume; it does not identify speakers.\n'
        'Useful for uneven podcasts or calls. Can make music swell or duck; usually leave off for music.'
    )).grid(row=14, column=0, columnspan=5, sticky='w', pady=(2, 8))

    globals_frame = ttk.Frame(frame)
    globals_frame.grid(row=15, column=0, columnspan=5, sticky='ew')
    globals_frame.columnconfigure(0, weight=1)
    globals_frame.columnconfigure(2, weight=1)
    globals_vars, voice_fields = {}, []
    for index, (name, label, value, low, high, step) in enumerate((
        ('output_db', 'Output gain (dB)', initial.output_db, -24, 18, 1),
        ('target_db', 'Voice level target (RMS dBFS)', initial.target_db, -36, -10, 1),
        ('max_boost_db', 'Voice boost limit (dB)', initial.max_boost_db, 0, 24, 1),
        ('level_ms', 'Voice leveling time (ms)', initial.level_ms, 100, 10000, 100))):
        row, column = divmod(index, 2)
        column *= 2
        ttk.Label(globals_frame, text=label).grid(row=row, column=column, sticky='w', pady=3)
        var = tk.StringVar(value=f'{value:g}')
        globals_vars[name] = (var, low, high)
        entry = ttk.Spinbox(globals_frame, from_=low, to=high, increment=step,
                            textvariable=var, width=9)
        entry.grid(row=row, column=column+1, sticky='e', padx=(8, 18), pady=3)
        if name != 'output_db':
            voice_fields.append(entry)

    ttk.Label(frame, textvariable=meters).grid(row=16, column=0, columnspan=5, sticky='w', pady=(12, 5))
    ttk.Label(frame, textvariable=status, style='Muted.TLabel', wraplength=860).grid(
        row=17, column=0, columnspan=5, sticky='w')

    def params():
        p = Params.default()
        for band, values in enumerate(band_vars):
            for name, var, low, high in zip(('threshold', 'ratio', 'release_ms'), values,
                                          (-60, 1, 10), (0, 1000, 5000)):
                value = float(var.get())
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f'{BAND_NAMES[band]} {name} must be {low}…{high}')
                getattr(p, name)[band] = value
        for name, (var, low, high) in globals_vars.items():
            value = float(var.get())
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'{name} must be {low}…{high}')
            setattr(p, name, value)
        p.auto_level, p.bypass = float(auto.get()), float(bypass.get())
        return p

    applying = False

    def edited(*_):
        for widget in voice_fields:
            widget['state'] = 'normal' if auto.get() else 'disabled'
        if not applying:
            selected_preset.set('Custom')
            preset_description.set('Custom settings. Choose a preset to replace the audio controls with its starting values.')

    def choose_preset(_=None):
        nonlocal applying
        key = next((k for k, p in PRESETS.items() if p.label == selected_preset.get()), None)
        if key is None:
            return
        applying = True
        try:
            p = get_preset(key)
            for band, values in enumerate(band_vars):
                for var, name in zip(values, ('threshold', 'ratio', 'release_ms')):
                    var.set(f'{getattr(p, name)[band]:g}')
            for name, (var, _, _) in globals_vars.items():
                var.set(f'{getattr(p, name):g}')
            auto.set(bool(p.auto_level))
            preset_description.set(PRESETS[key].description)
        finally:
            applying = False

    for values in band_vars:
        for var in values:
            var.trace_add('write', edited)
    for var, _, _ in globals_vars.values():
        var.trace_add('write', edited)
    auto.trace_add('write', edited)
    preset_selector.bind('<<ComboboxSelected>>', choose_preset)
    applying = True
    edited()
    applying = False
    if bytes(initial) != bytes(get_preset(preset_key)):
        edited()

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
    button.grid(row=18, column=0, columnspan=2, sticky='w', pady=(12, 0))
    ttk.Button(frame, text='Refresh outputs', command=refresh).grid(
        row=18, column=3, columnspan=2, sticky='e', pady=(12, 0))

    def tick():
        if session.route:
            try:
                for kind, value in session.poll():
                    if kind == 'error':
                        stop()
                        status.set('Audio stopped: ' + value)
                        break
                    if kind == 'ready':
                        status.set('Running. Controls and presets apply live. Lower output gain if the meter exceeds 0 dBFS.')
                    if kind == 'meters':
                        meters.set(format_meters(value))
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

    try:
        recovered = recover_stale_routes(output)
        if recovered:
            status.set('Recovered playback from an abandoned EvenAudio output.')
    except Exception as exc:
        status.set('Could not check for abandoned audio outputs: ' + str(exc))
    refresh()
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(100, tick)
    try:
        root.mainloop()
    finally:
        session.stop()
