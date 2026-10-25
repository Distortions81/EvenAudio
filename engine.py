"""DSP binding and synchronous stereo PulseAudio I/O (also pipewire-pulse)."""
import ctypes as C
from pathlib import Path
import queue

RATE = 48000
FRAMES = 480
BANDS = 7
DEFAULT_RELEASE_MS = (2000, 1414, 1000, 707, 500, 354, 250)
BAND_NAMES = ('<80 Hz', '80–200', '200–500', '500–1.25k', '1.25–3.15k', '3.15–8k', '>8 kHz')


class Params(C.Structure):
    _fields_ = [(n, C.c_float * BANDS) for n in ('threshold', 'ratio', 'release_ms')] + [
        (n, C.c_float) for n in ('output_db', 'auto_level', 'target_db',
                                'max_boost_db', 'level_ms', 'bypass')]

    @classmethod
    def default(cls):
        p = cls()
        for b in range(BANDS):
            p.threshold[b], p.ratio[b], p.release_ms[b] = -24, 10, DEFAULT_RELEASE_MS[b]
        p.output_db = 6
        p.auto_level, p.target_db, p.max_boost_db, p.level_ms = 0, -18, 18, 1500
        return p


class Meters(C.Structure):
    _fields_ = [(n, C.c_float) for n in ('input_db', 'output_db', 'auto_db')] + [
        ('reduction', C.c_float * BANDS)]


class DSP:
    def __init__(self, rate=RATE):
        path = Path(__file__).with_name('libevenaudio.so')
        if not path.exists():
            raise RuntimeError('DSP library missing. Run make first.')
        self.lib = C.CDLL(str(path))
        self.lib.ea_create.argtypes = [C.c_double]
        self.lib.ea_create.restype = C.c_void_p
        self.lib.ea_destroy.argtypes = [C.c_void_p]
        self.lib.ea_process.argtypes = [C.c_void_p, C.POINTER(C.c_float), C.c_size_t,
                                       C.POINTER(Params), C.POINTER(Meters)]
        self.ptr = self.lib.ea_create(rate)
        if not self.ptr:
            raise ValueError('Unsupported sample rate or allocation failure')

    def process(self, buffer, params):
        if len(buffer) % 2:
            raise ValueError('Expected interleaved stereo frames')
        meters = Meters()
        self.lib.ea_process(self.ptr, buffer, len(buffer) // 2,
                            C.byref(params), C.byref(meters))
        return meters

    def close(self):
        if self.ptr:
            self.lib.ea_destroy(self.ptr)
            self.ptr = None


class SampleSpec(C.Structure):
    _fields_ = [('format', C.c_int), ('rate', C.c_uint32), ('channels', C.c_uint8)]


class BufferAttr(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in ('maxlength', 'tlength', 'prebuf', 'minreq', 'fragsize')]


class Pulse:
    def __init__(self, device, record):
        import sys
        self.lib = C.CDLL('libpulse-simple.so.0')
        self.base = C.CDLL('libpulse.so.0')
        self.base.pa_strerror.argtypes = [C.c_int]
        self.base.pa_strerror.restype = C.c_char_p
        self.lib.pa_simple_new.argtypes = [C.c_char_p, C.c_char_p, C.c_int, C.c_char_p,
            C.c_char_p, C.POINTER(SampleSpec), C.c_void_p, C.POINTER(BufferAttr), C.POINTER(C.c_int)]
        self.lib.pa_simple_new.restype = C.c_void_p
        for name in ('pa_simple_read', 'pa_simple_write'):
            getattr(self.lib, name).argtypes = [C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_int)]
            getattr(self.lib, name).restype = C.c_int
        self.lib.pa_simple_free.argtypes = [C.c_void_p]
        # PA_SAMPLE_FLOAT32LE=5, PA_SAMPLE_FLOAT32BE=6; playback=1, record=2.
        spec = SampleSpec(5 if sys.byteorder == 'little' else 6, RATE, 2)
        auto = 0xffffffff
        attr = BufferAttr(auto, FRAMES * 8 * 3, 0, FRAMES * 8, FRAMES * 8)
        error = C.c_int()
        self.ptr = self.lib.pa_simple_new(None, b'EvenAudio', 2 if record else 1,
            device.encode(), b'EvenAudio capture' if record else b'EvenAudio output',
            C.byref(spec), None, C.byref(attr), C.byref(error))
        if not self.ptr:
            raise RuntimeError(self.base.pa_strerror(error.value).decode())
        self.record = record

    def transfer(self, buffer):
        error = C.c_int()
        fn = self.lib.pa_simple_read if self.record else self.lib.pa_simple_write
        if fn(self.ptr, buffer, C.sizeof(buffer), C.byref(error)) < 0:
            raise RuntimeError(self.base.pa_strerror(error.value).decode())

    def close(self):
        if self.ptr:
            self.lib.pa_simple_free(self.ptr)
            self.ptr = None


def audio_worker(source, sink, controls, messages, stop, initial):
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Parent owns Ctrl+C and routing cleanup.
    capture = playback = dsp = None
    try:
        dsp = DSP()
        playback = Pulse(sink, False)
        capture = Pulse(source, True)
        p = Params.from_buffer_copy(initial)
        buffer = (C.c_float * (FRAMES * 2))()
        messages.put(('ready', None))
        count = 0
        interval = Meters(-240, -240, 0, (C.c_float * BANDS)())
        while not stop.is_set():
            try:
                while True:
                    p = Params.from_buffer_copy(controls.get_nowait())
            except queue.Empty:
                pass
            capture.transfer(buffer)
            m = dsp.process(buffer, p)
            playback.transfer(buffer)
            interval.input_db = max(interval.input_db, m.input_db)
            interval.output_db = max(interval.output_db, m.output_db)
            interval.auto_db = m.auto_db
            for b in range(BANDS):
                interval.reduction[b] = max(interval.reduction[b], m.reduction[b])
            count += 1
            if count % 10 == 0:
                try:
                    messages.put_nowait(('meters', bytes(interval)))
                except queue.Full:
                    pass
                interval = Meters(-240, -240, 0, (C.c_float * BANDS)())
    except Exception as exc:
        try:
            messages.put(('error', str(exc)), timeout=1)
        except queue.Full:
            pass
    finally:
        for obj in (capture, playback, dsp):
            if obj:
                obj.close()
