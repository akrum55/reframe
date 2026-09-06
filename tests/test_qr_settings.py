"""Austin's manual-QR defaults and existing-settings regression coverage."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import dashboard


class QrSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.manager = dashboard.SettingsManager(
            str(Path(self.directory.name) / "settings.json"))

    def tearDown(self):
        self.directory.cleanup()

    def test_new_settings_disable_automatic_qr(self):
        self.assertIs(self.manager.load_settings()["system"][
            "show_dashboard_qr_on_wifi_connect"], False)

    def test_narrow_qr_change_preserves_existing_camera_settings(self):
        self.assertTrue(self.manager.save_settings({
            "camera": {"exposure_value": 1},
            "system": {"show_dashboard_qr_on_wifi_connect": True}}))
        self.assertTrue(self.manager.save_settings({
            "system": {"show_dashboard_qr_on_wifi_connect": False}}))
        settings = self.manager.load_settings()
        self.assertEqual(settings["camera"]["exposure_value"], 1)
        self.assertIs(settings["system"]["show_dashboard_qr_on_wifi_connect"], False)

    def test_saved_opt_in_requires_explicit_deployment_change(self):
        self.assertTrue(self.manager.save_settings({
            "system": {"show_dashboard_qr_on_wifi_connect": True}}))
        self.assertIs(self.manager.load_settings()["system"][
            "show_dashboard_qr_on_wifi_connect"], True)

    def test_example_settings_disable_automatic_qr(self):
        example = Path(__file__).resolve().parents[1] / "settings.example.json"
        settings = json.loads(example.read_text())
        self.assertIs(settings["system"]["show_dashboard_qr_on_wifi_connect"], False)

    def test_dashboard_explains_controls_and_resets_to_manual_qr(self):
        response = asyncio.run(dashboard.dashboard())
        html = response.body.decode("utf-8")
        self.assertIn("three short button presses", html)
        self.assertIn("show_dashboard_qr_on_wifi_connect: false", html)


if __name__ == "__main__":
    unittest.main()
