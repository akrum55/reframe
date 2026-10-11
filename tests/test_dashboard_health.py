"""Upstream health/fast-mode integration with Austin's library and settings."""

import asyncio
import copy
import json
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import dashboard


class DashboardHealthTests(unittest.TestCase):
    storage = {"total": 32 * 1024 ** 3, "free": 24 * 1024 ** 3}

    def test_live_health_uses_current_diagnostics_not_stale_snapshot(self):
        report = dashboard.build_health_report(
            {"success": True, "diagnostics": {"issues": {}, "last_activity": "Photo captured"}},
            {"issues": {"camera": "Camera not detected"}}, self.storage,
            {"battery_level": 80},
        )
        self.assertTrue(report["system_online"])
        self.assertEqual(report["headline"], "system online")
        self.assertTrue(report["service"]["ok"])
        self.assertEqual(report["last_activity"], "Photo captured")
        self.assertEqual(report["storage"], "24.0 GB free (25% used)")

    def test_offline_health_retains_last_known_issue(self):
        report = dashboard.build_health_report(
            None, {"issues": {"camera": "Camera not detected"},
                   "last_activity": {"message": "Camera initialization failed"}},
            self.storage, {"battery_level": None},
        )
        self.assertFalse(report["system_online"])
        self.assertFalse(report["service"]["ok"])
        self.assertEqual(report["headline"], "camera not detected")
        self.assertEqual(report["last_activity"], "Camera initialization failed")

    def test_empty_and_malformed_issue_snapshots_do_not_break_health(self):
        for issues in (None, [], "invalid", 1, {}):
            with self.subTest(issues=issues):
                report = dashboard.build_health_report(
                    None, {"issues": issues}, self.storage, {"battery_level": None})
                self.assertEqual(report["headline"], "camera service unavailable")

    def test_low_storage_and_battery_are_reported(self):
        report = dashboard.build_health_report(
            {"success": True}, {}, {"total": 32 * 1024 ** 3, "free": 100},
            {"battery_level": 4},
        )
        self.assertFalse(report["system_online"])
        self.assertEqual(report["headline"], "storage full")
        self.assertEqual(len(report["issues"]), 2)
        self.assertIn("Battery critically low (4%)", report["issues"])

    def test_health_endpoint_falls_back_without_user_activity_or_mutation(self):
        usage = namedtuple("Usage", "total used free")(
            32 * 1024 ** 3, 8 * 1024 ** 3, 24 * 1024 ** 3)
        with TestClient(dashboard.app) as client, \
             patch.object(dashboard.reframe_client, "get", new_callable=AsyncMock,
                          side_effect=RuntimeError("offline")) as get, \
             patch.object(dashboard.reframe_client, "post", new_callable=AsyncMock) as post, \
             patch.object(dashboard, "read_battery_level", return_value={"battery_level": 70}), \
             patch.object(dashboard, "load_diagnostics", return_value={"issues": {"button": "Read failed"}}), \
             patch.object(dashboard.shutil, "disk_usage", return_value=usage) as disk:
            response = client.get("/api/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["headline"], "button not responding")
            get.assert_awaited_once_with("/status")
            post.assert_not_called()
            disk.assert_called_once_with(dashboard.PHOTOS_PATH)

    def test_merged_page_has_both_library_and_health_controls(self):
        html = asyncio.run(dashboard.dashboard()).body.decode()
        for marker in ("/assets/photo-library.js", "storage-info", "health-section",
                       "fast-refresh", "show-dashboard-qr-on-wifi-connect"):
            self.assertIn(marker, html)
        self.assertNotIn("updateBatteryLevel", html)
        self.assertEqual(html.count("setInterval(updateHealth, 30000)"), 1)
        self.assertEqual(html.count("setInterval(updateStorageInfo, 30000)"), 1)
        self.assertIn("fast_refresh: true", html)
        self.assertIn("show_dashboard_qr_on_wifi_connect: false", html)


class FastModeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = dashboard.SettingsManager(str(Path(self.temp.name) / "settings.json"))

    def tearDown(self):
        self.temp.cleanup()

    def test_fast_mode_default_is_enabled_and_manual_qr_remains_default(self):
        settings = self.manager.load_settings()
        self.assertIs(settings["display"]["fast_refresh"], True)
        self.assertIs(settings["system"]["show_dashboard_qr_on_wifi_connect"], False)

    def test_missing_fast_setting_uses_default_without_overwriting_explicit_false(self):
        saved = self.manager.load_settings()
        del saved["display"]["fast_refresh"]
        self.manager.settings_path.write_text(json.dumps(saved))
        self.assertIs(self.manager.load_settings()["display"]["fast_refresh"], True)
        self.assertTrue(self.manager.save_settings({"display": {"fast_refresh": False}}))
        self.assertIs(self.manager.load_settings()["display"]["fast_refresh"], False)

    def test_narrow_fast_setting_preserves_other_settings(self):
        before = copy.deepcopy(self.manager.load_settings())
        self.assertTrue(self.manager.save_settings({"display": {"fast_refresh": False}}))
        after = self.manager.load_settings()
        expected = copy.deepcopy(before)
        expected["display"]["fast_refresh"] = False
        self.assertEqual(after, expected)

    def test_fast_setting_requires_boolean(self):
        for invalid in ("true", 1, None):
            with self.subTest(invalid=invalid), self.assertRaises(dashboard.SettingsValidationError):
                self.manager.save_settings({"display": {"fast_refresh": invalid}})


class DiagnosticLogTests(unittest.TestCase):
    def test_tail_includes_rotated_logs_and_limits_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reframe.log"
            path.with_suffix(".log.1").write_text("old\n")
            path.write_text("one\ntwo\nthree\n")
            with patch.object(dashboard, "REFRAME_LOG_PATH", str(path)):
                self.assertEqual(dashboard.tail_reframe_log(3), "one\ntwo\nthree\n")
                self.assertEqual(dashboard.tail_reframe_log(4), "old\none\ntwo\nthree\n")

    def test_long_log_read_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reframe.log"
            path.write_bytes(b"x" * 100000 + b"\nDisplay refresh completed\n")
            with patch.object(dashboard, "REFRAME_LOG_PATH", str(path)):
                self.assertEqual(dashboard.tail_reframe_log(), "Display refresh completed\n")

    def test_redacts_network_paths_credentials_and_known_token_values(self):
        content = ("Display refresh completed in 6.4s\n"
                   "Dashboard QR: http://192.0.2.42:80/\n"
                   "Photo saved to /home/fixture/photos/00001.jpg\n"
                   "Wi-Fi connected to private-fixture-network\n"
                   "password=fixture-password\n"
                   "Remote returned fixture-token\n"
                   "Peer 192.0.2.42:8080 and 2001:db8::1\n"
                   "IPv6 2001:db8:: and fe80::1%eth0\n")
        settings = {"extensions": {"arena": {"access_token": "fixture-token"}}}
        result = dashboard.redact_diagnostic_log(content, settings)
        self.assertIn("Display refresh completed in 6.4s", result)
        for private in ("192.0.2.42", "2001:db8::", "fe80::1", "/home/fixture", "fixture-password",
                        "fixture-token", "private-fixture-network"):
            self.assertNotIn(private, result)

    def test_log_endpoint_is_uncached_and_redacted(self):
        with TestClient(dashboard.app) as client, \
             patch.object(dashboard, "tail_reframe_log", return_value="Remote returned fixture-token\n"), \
             patch.object(dashboard.settings_manager, "load_settings",
                          return_value={"extensions": {"arena": {"access_token": "fixture-token"}}}):
            response = client.get("/api/diagnostics/logs")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertIn("text/plain", response.headers["content-type"])
            self.assertNotIn("fixture-token", response.text)


if __name__ == "__main__":
    unittest.main()
