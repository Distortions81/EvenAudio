from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import evenaudio


class StartupTests(unittest.TestCase):
    def launch(self, *args):
        with patch.object(sys, 'argv', ['evenaudio.py', *args]), \
                patch.object(evenaudio, 'gui') as gui, \
                patch.object(evenaudio.signal, 'signal'):
            self.assertEqual(evenaudio.main(), 0)
            return list(gui.call_args.args[0].release_ms)

    def test_normal_start_preserves_frequency_dependent_releases(self):
        self.assertEqual(self.launch(), [2000, 1414, 1000, 707, 500, 354, 250])

    def test_explicit_release_still_overrides_every_band(self):
        self.assertEqual(self.launch('--release', '90'), [90] * 7)

    def test_music_default_and_opt_in_speaker_leveling(self):
        for arguments, expected in (([], 0), (['--auto-level'], 1), (['--no-auto-level'], 0)):
            with self.subTest(arguments=arguments), \
                    patch.object(sys, 'argv', ['evenaudio.py', *arguments]), \
                    patch.object(evenaudio, 'gui') as gui, \
                    patch.object(evenaudio.signal, 'signal'):
                self.assertEqual(evenaudio.main(), 0)
                self.assertEqual(gui.call_args.args[0].auto_level, expected)
