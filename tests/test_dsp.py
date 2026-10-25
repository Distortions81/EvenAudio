import ctypes as C
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import DSP, Params, RATE, FRAMES


def tone(hz, amplitude, seconds, right=None):
    frames = round(seconds * RATE)
    buf = (C.c_float * (frames * 2))()
    for i in range(frames):
        value = amplitude * math.sin(2 * math.pi * hz * i / RATE)
        buf[2*i], buf[2*i+1] = value, value if right is None else value * right
    return buf


def rms(buf, start=0, channel=0):
    values = buf[2*start+channel::2]
    return math.sqrt(sum(x*x for x in values) / len(values))


class DSPTests(unittest.TestCase):
    def setUp(self):
        self.dsp = DSP()
        self.p = Params.default()
        self.p.auto_level = 0
        self.p.output_db = 0

    def tearDown(self):
        self.dsp.close()

    def test_unity_crossover_response_across_spectrum(self):
        for b in range(7):
            self.p.ratio[b] = 1
        for frequency in (35, 80, 125, 200, 350, 500, 900, 1250, 2000, 3150, 5000, 8000, 14000):
            with self.subTest(frequency=frequency):
                signal = tone(frequency, .1, .3)
                self.dsp.process(signal, self.p)
                error_db = 20 * math.log10(rms(signal, RATE // 5) / (.1 / math.sqrt(2)))
                self.assertLess(abs(error_db), .08)

    def test_stereo_link_preserves_channel_ratio(self):
        signal = tone(900, .8, .4, right=.25)
        self.dsp.process(signal, self.p)
        self.assertAlmostEqual(rms(signal, 5000, 1) / rms(signal, 5000, 0), .25, places=6)

    def test_ten_to_one_reduces_twenty_db_level_difference(self):
        def measure(amplitude):
            processor = DSP()
            try:
                signal = tone(900, amplitude, .6)
                processor.process(signal, self.p)
                return rms(signal, RATE // 3)
            finally:
                processor.close()
        # Both levels are above threshold in the principal band.
        for b in range(7):
            self.p.threshold[b] = -45
        difference = 20 * math.log10(measure(.8) / measure(.08))
        self.assertGreater(difference, 1)
        self.assertLess(difference, 4)

    def test_first_nonzero_sample_is_compressed(self):
        unity = DSP()
        try:
            dry = (C.c_float * 2)(1, 1)
            wet = (C.c_float * 2)(1, 1)
            p = Params.from_buffer_copy(bytes(self.p))
            for b in range(7):
                p.ratio[b] = 1
                self.p.threshold[b] = -40
            unity.process(dry, p)
            self.dsp.process(wet, self.p)
            self.assertGreater(abs(dry[0]), .001)
            self.assertLess(abs(wet[0]), abs(dry[0]) * .5)
        finally:
            unity.close()

    def test_release_time_changes_recovery(self):
        def residual(release):
            processor = DSP()
            p = Params.from_buffer_copy(bytes(self.p))
            for b in range(7):
                p.release_ms[b] = release
            try:
                processor.process(tone(900, .8, .2), p)
                processor.process((C.c_float * (RATE // 5 * 2))(), p)
                return processor.process((C.c_float * 960)(), p).reduction[3]
            finally:
                processor.close()
        fast, slow = residual(30), residual(500)
        self.assertLess(fast, .2)
        self.assertGreater(slow, 4)

    def test_slow_bass_default_reduces_between_beat_gain_movement(self):
        def movement(release):
            processor = DSP()
            p = Params.default()
            p.output_db = 0
            p.release_ms[0] = release
            early, late, middle = [], [], []
            try:
                # Sustained bass + 120 BPM decaying kick + steady midrange tone.
                for block in range(400):
                    signal = (C.c_float * (FRAMES * 2))()
                    for i in range(FRAMES):
                        t = (block * FRAMES + i) / RATE
                        beat = t % .5
                        kick = .6 * math.exp(-beat / .025) if beat < .12 else 0
                        value = ((.025 + kick) * math.sin(2 * math.pi * 50 * t)
                                 + .02 * math.sin(2 * math.pi * 900 * t))
                        signal[2*i] = signal[2*i+1] = value
                    meters = processor.process(signal, p)
                    if block >= 300:
                        beat = (block * FRAMES / RATE) % .5
                        if .18 <= beat < .23:
                            early.append(meters.reduction[0])
                        if .4 <= beat < .45:
                            late.append(meters.reduction[0])
                        middle.append(meters.reduction[3])
                self.assertLess(max(middle), .1)
                return sum(early) / len(early) - sum(late) / len(late)
            finally:
                processor.close()
        fast = movement(120)
        slow = movement(Params.default().release_ms[0])
        self.assertGreater(fast, 2.5)
        self.assertLess(slow, fast * .6)

    def test_silence_does_not_increase_auto_gain(self):
        self.p.auto_level = 1
        self.dsp.process(tone(900, .025, 2), self.p)
        silent = (C.c_float * (RATE * 3 * 2))()
        before = self.dsp.process((C.c_float * 2)(), self.p).auto_db
        after = self.dsp.process(silent, self.p).auto_db
        self.assertAlmostEqual(before, after, places=5)

    def test_quiet_speaker_gets_more_auto_gain(self):
        self.p.auto_level = 1
        quiet = self.dsp.process(tone(900, .025, 4), self.p).auto_db
        loud = self.dsp.process(tone(900, .4, 4), self.p).auto_db
        self.assertGreater(quiet - loud, 15)
        self.assertLessEqual(quiet, self.p.max_boost_db)

    def test_bypass_settles_to_original_samples(self):
        self.p.bypass = 1
        signal = tone(900, .8, .3)
        original = list(signal)
        self.dsp.process(signal, self.p)
        self.assertLess(max(abs(a-b) for a,b in zip(signal[24000:], original[24000:])), .0001)

    def test_nonfinite_input_does_not_poison_filters(self):
        signal = (C.c_float * 6)(float('nan'), float('inf'), 0, 0, .1, .1)
        self.dsp.process(signal, self.p)
        self.assertTrue(all(math.isfinite(x) for x in signal))


if __name__ == '__main__':
    unittest.main()
