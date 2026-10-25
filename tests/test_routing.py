from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routing import Routing


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.default = 'speakers'
        self.sinks = [{'index': 1, 'name': 'speakers'}, {'index': 2, 'name': 'headphones'}]
        self.streams = []
        self.patch_list = patch('routing.listing', side_effect=lambda kind:
                                self.sinks if kind == 'sinks' else self.streams)
        self.patch_pactl = patch('routing.pactl', side_effect=self.command)
        self.patch_list.start()
        self.patch_pactl.start()
        self.addCleanup(self.patch_list.stop)
        self.addCleanup(self.patch_pactl.stop)

    def command(self, *args):
        self.calls.append(args)
        if args[0] == 'get-default-sink':
            return self.default
        if args[0] == 'set-default-sink':
            self.default = args[1]
        if args[0] == 'load-module':
            return '42'
        return ''

    def test_only_selected_output_moves_and_dsp_is_excluded(self):
        r = Routing()
        r.create()
        self.sinks.append({'index': 3, 'name': r.name})
        self.streams = [
            {'index': 10, 'sink': 1, 'properties': {}},
            {'index': 11, 'sink': 2, 'properties': {}},
            {'index': 12, 'sink': 1, 'properties': {'application.name': 'EvenAudio'}}]
        r.activate()
        moves = [call for call in self.calls if call[0] == 'move-sink-input']
        self.assertEqual(moves, [('move-sink-input', '10', r.name),
                                 ('move-sink-input', '11', 'headphones'),
                                 ('move-sink-input', '12', 'speakers')])

    def test_cleanup_restores_default_and_current_virtual_streams(self):
        r = Routing()
        r.create()
        self.sinks.append({'index': 3, 'name': r.name})
        self.default = r.name
        self.streams = [{'index': 20, 'sink': 3, 'properties': {}}]
        self.assertEqual(r.close(), [])
        self.assertEqual(self.default, 'speakers')
        self.assertIn(('move-sink-input', '20', 'speakers'), self.calls)
        self.assertIn(('unload-module', '42'), self.calls)

    def test_cleanup_preserves_user_changed_default_and_manual_moves(self):
        r = Routing()
        r.create()
        self.sinks.append({'index': 3, 'name': r.name})
        r.moved['10'] = 'speakers'
        self.default = 'headphones'
        self.streams = [{'index': 10, 'sink': 2, 'properties': {}}]
        self.assertEqual(r.close(), [])
        self.assertEqual(self.default, 'headphones')
        self.assertFalse(any(c[0] == 'move-sink-input' for c in self.calls))

    def test_cannot_route_output_into_an_evenaudio_sink(self):
        self.sinks.append({'index': 3, 'name': 'evenaudio_other'})
        with self.assertRaisesRegex(RuntimeError, 'Choose speakers'):
            Routing('evenaudio_other')
