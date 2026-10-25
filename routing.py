"""Temporary virtual sink with explicit output pinning and reversible routing."""
import json
import subprocess
import uuid


def pactl(*args):
    try:
        return subprocess.check_output(['pactl', *args], text=True,
                                       stderr=subprocess.PIPE, timeout=5).strip()
    except FileNotFoundError as exc:
        raise RuntimeError('pactl is missing; install pulseaudio-utils / pulseaudio') from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or 'Audio server unavailable') from exc


def listing(kind):
    return json.loads(pactl('--format=json', 'list', kind))


class Routing:
    def __init__(self, output=None):
        self.original = pactl('get-default-sink')
        self.output = output or self.original
        sinks = listing('sinks')
        target = next((s for s in sinks if s['name'] == self.output), None)
        if not target:
            raise RuntimeError('Output sink no longer exists. Refresh the output list.')
        if self.output.startswith('evenaudio_'):
            raise RuntimeError('Choose speakers/headphones, not another EvenAudio virtual sink.')
        self.target_index = str(target['index'])
        self.name = 'evenaudio_' + uuid.uuid4().hex[:10]
        self.module = None
        self.moved = {}
        self.worker_pid = None

    def create(self):
        self.module = pactl('load-module', 'module-null-sink', 'sink_name=' + self.name,
            'rate=48000', 'channels=2', 'channel_map=front-left,front-right',
            'sink_properties=device.description=EvenAudio')
        # Prevent feedback while server default-following policy moves streams.
        pactl('suspend-sink', self.name, '1')

    def activate(self):
        # Called only once the DSP playback and capture streams are connected.
        sinks = {str(s['index']): s['name'] for s in listing('sinks')}
        before = listing('sink-inputs')
        pactl('set-default-sink', self.name)
        for stream in before:
            props = stream.get('properties', {})
            ours = props.get('application.name') == 'EvenAudio' and (
                self.worker_pid is None or props.get('application.process.id') == str(self.worker_pid))
            index = str(stream['index'])
            if ours:
                # Default-following policy can move even explicitly opened streams.
                pactl('move-sink-input', index, self.output)
                continue
            old = sinks.get(str(stream['sink']))
            if old != self.output:
                if old:
                    try:
                        pactl('move-sink-input', index, old)
                    except RuntimeError:
                        pass
                continue
            try:
                pactl('move-sink-input', index, self.name)
                self.moved[index] = old
            except RuntimeError:
                pass  # Stream may have ended during enumeration.
        pactl('suspend-sink', self.name, '0')

    def close(self):
        errors = []
        if self.module is None:
            return errors
        try:
            sinks = listing('sinks')
            names = {s['name'] for s in sinks}
            by_index = {str(s['index']): s['name'] for s in sinks}
            fallback = self.original if self.original in names else self.output
            if fallback not in names:
                fallback = next((n for n in names if not n.startswith('evenaudio_')), None)
            if not fallback:
                raise RuntimeError('No physical output remains for restoring playback')
            if pactl('get-default-sink') == self.name:
                pactl('set-default-sink', fallback)
            for stream in listing('sink-inputs'):
                index = str(stream['index'])
                # Do not undo a manual move the user made while running.
                if by_index.get(str(stream['sink'])) != self.name:
                    continue
                destination = self.moved.get(index, self.output)
                if destination not in names:
                    destination = fallback
                try:
                    pactl('move-sink-input', index, destination)
                except RuntimeError as exc:
                    errors.append(str(exc))
        except Exception as exc:
            errors.append(str(exc))
        try:
            pactl('unload-module', self.module)
            self.module = None
        except Exception as exc:
            errors.append(str(exc))
        return errors
