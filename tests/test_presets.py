import ctypes as C
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import DSP, Params
from presets import PRESETS, get_preset


class PresetTests(unittest.TestCase):
    def test_general_and_engine_defaults_agree(self):
        self.assertEqual(bytes(get_preset('general')), bytes(Params.default()))

    def test_recipes_process_finite_audio_and_have_slow_music_bass(self):
        for key in PRESETS:
            with self.subTest(preset=key):
                p = get_preset(key)
                if key != 'uneven-voices':
                    self.assertEqual(p.auto_level, 0)
                    self.assertGreaterEqual(p.release_ms[0], 2000)
                self.assertTrue(all(10 <= t <= 5000 for t in p.release_ms))
                processor = DSP()
                try:
                    audio = (C.c_float * 960)(*[.2 * math.sin(i) for i in range(960)])
                    processor.process(audio, p)
                    self.assertTrue(all(math.isfinite(x) for x in audio))
                finally:
                    processor.close()

    def test_tweaking_recipe_does_not_change_later_selections(self):
        original = bytes(get_preset('general'))
        modified = get_preset('general')
        modified.release_ms[0] = 10
        self.assertEqual(bytes(get_preset('general')), original)
