"""Exercise actual display methods with no hardware imports or register writes."""
import ast
import json
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def isolated_class(filename, name, namespace):
    tree = ast.parse((ROOT / filename).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), namespace)
    return namespace[name]


class FastRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Path(self.temp.name) / 'settings.json'
        display_class = isolated_class('reframe.py', 'EInkDisplay', {
            'json': json, 'threading': threading, 'logging': logging})
        self.display = display_class(str(self.settings))
        self.display.epd = Mock()
        self.display._initialized = True

    def tearDown(self):
        self.temp.cleanup()

    def save(self, data):
        self.settings.write_text(json.dumps(data))

    def test_explicit_choice_is_read_again_each_refresh(self):
        for choice in (True, False, True):
            self.save({'display': {'fast_refresh': choice}})
            self.display.display_buffer([1, 2])
            self.display.epd.display.assert_called_with([1, 2], fast_refresh=choice)

    def test_valid_settings_missing_choice_use_custom_default(self):
        for data in ({}, {'display': {}}, {'camera': {'fixture': 1}}):
            self.save(data)
            self.assertIs(self.display._fast_refresh_enabled(), True)

    def test_missing_file_and_malformed_input_fail_safe_standard(self):
        self.assertIs(self.display._fast_refresh_enabled(), False)
        for data in ({'display': None}, {'display': {'fast_refresh': 'true'}},
                     {'display': {'fast_refresh': 1}}, [], None):
            self.save(data)
            self.assertIs(self.display._fast_refresh_enabled(), False)
        self.settings.write_text('{bad json')
        self.assertIs(self.display._fast_refresh_enabled(), False)

    def test_driver_selects_clock_each_cycle_and_returns_to_standard(self):
        driver_class = isolated_class('waveshare_epd/epd4in0e.py', 'EPD', {
            'epdconfig': Mock(), 'logger': logging.getLogger(__name__)})
        driver = driver_class.__new__(driver_class)
        driver.send_command = Mock()
        driver.send_data = Mock()
        driver.ReadBusyH = Mock()
        for fast, clock in ((True, 0x07), (False, 0x08), (True, 0x07)):
            driver.send_command.reset_mock()
            driver.send_data.reset_mock()
            driver.TurnOnDisplay(fast_refresh=fast)
            self.assertEqual(driver.send_command.call_args_list[0].args, (0x30,))
            self.assertEqual(driver.send_data.call_args_list[0].args, (clock,))

    def test_example_uses_explicitly_requested_fast_default(self):
        settings = json.loads((ROOT / 'settings.example.json').read_text())
        self.assertIs(settings['display']['fast_refresh'], True)
        self.assertIs(settings['system']['show_dashboard_qr_on_wifi_connect'], False)
