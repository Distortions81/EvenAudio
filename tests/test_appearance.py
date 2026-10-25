import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from appearance import load_theme, preference_path, save_theme


class AppearanceTests(unittest.TestCase):
    def test_dark_default_light_preference_and_corrupt_file_fallback(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CONFIG_HOME': temp}):
            self.assertEqual(load_theme(), 'dark')
            self.assertTrue(save_theme('light'))
            self.assertEqual(load_theme(), 'light')
            preference_path().write_text('{broken', encoding='utf-8')
            self.assertEqual(load_theme(), 'dark')

    def test_unwritable_configuration_does_not_break_theme_switch(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'not-a-directory'
            file.write_text('x')
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(file)}):
                self.assertFalse(save_theme('light'))
