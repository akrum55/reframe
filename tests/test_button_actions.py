"""Exercise asynchronous button dispatch without importing camera hardware."""

import threading
import unittest
from unittest.mock import patch

from button_actions import ButtonActionDispatcher


class ButtonActionDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.operation_lock = threading.Lock()
        self.display_busy = threading.Event()
        self.calls = []
        self.activity = []
        self.dispatcher = ButtonActionDispatcher(
            self.operation_lock,
            self.display_busy.is_set,
            {"capture": self.capture, "qr": self.qr},
            self.update_activity,
        )

    def update_activity(self):
        self.activity.append("activity")

    def capture(self):
        self.calls.append("capture")
        return {"success": True}

    def qr(self):
        self.calls.append("qr")
        return {"success": True}

    def assert_idle(self):
        self.assertFalse(self.operation_lock.locked())
        self.assertFalse(self.dispatcher.is_busy())

    def run_action(self, action):
        self.assertTrue(self.dispatcher.submit(action))
        self.dispatcher.wait(2)
        self.assert_idle()

    def test_single_and_triple_actions_call_only_the_mapped_handler(self):
        self.run_action("capture")
        self.assertEqual(self.calls, ["capture"])
        self.run_action("qr")
        self.assertEqual(self.calls, ["capture", "qr"])
        self.assertEqual(self.activity, ["activity", "activity"])

    def test_activity_is_updated_before_each_handler_under_operation_lock(self):
        observations = []

        def handler():
            observations.append((list(self.activity), self.operation_lock.locked()))
            return {"success": True}

        self.dispatcher = ButtonActionDispatcher(
            self.operation_lock, self.display_busy.is_set,
            {"capture": handler}, self.update_activity,
        )
        self.run_action("capture")
        self.assertEqual(observations, [(["activity"], True)])

    def test_submit_returns_while_qr_is_still_running_and_drops_other_actions(self):
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        submit_returned = threading.Event()
        submitted = []
        handler_threads = []

        def blocking_qr():
            handler_threads.append(threading.get_ident())
            self.calls.append("qr")
            entered.set()
            release.wait(3)
            completed.set()
            return {"success": True}

        self.dispatcher = ButtonActionDispatcher(
            self.operation_lock, self.display_busy.is_set,
            {"capture": self.capture, "qr": blocking_qr}, self.update_activity,
        )

        def submit_qr():
            submitted.append(self.dispatcher.submit("qr"))
            submit_returned.set()

        submitter = threading.Thread(target=submit_qr)
        submitter.start()
        try:
            self.assertTrue(entered.wait(1), "QR callback did not start")
            self.assertTrue(submit_returned.wait(1), "submit waited for QR completion")
            self.assertEqual(submitted, [True])
            self.assertNotEqual(handler_threads, [submitter.ident])
            self.assertFalse(completed.is_set())
            self.assertTrue(self.operation_lock.locked())
            self.assertTrue(self.dispatcher.is_busy())
            self.assertFalse(self.dispatcher.submit("capture"))
            self.assertFalse(self.dispatcher.submit("qr"))
            self.assertEqual(self.activity, ["activity"])
        finally:
            release.set()
            submitter.join(2)
            self.dispatcher.wait(2)

        self.assertTrue(completed.is_set())
        self.assertEqual(self.calls, ["qr"], "busy actions must not be queued")
        self.assert_idle()
        self.run_action("capture")
        self.assertEqual(self.calls, ["qr", "capture"])

    def test_display_refresh_drops_action_without_holding_operation_lock(self):
        self.display_busy.set()
        self.assertTrue(self.dispatcher.is_busy())
        self.assertFalse(self.dispatcher.submit("capture"))
        self.assertFalse(self.operation_lock.locked())
        self.assertEqual(self.activity, [])
        self.assertEqual(self.calls, [])
        self.display_busy.clear()
        self.assert_idle()
        self.run_action("qr")
        self.assertEqual(self.calls, ["qr"])

    def test_api_operation_lock_drops_action_without_releasing_api_lock(self):
        self.operation_lock.acquire()
        try:
            self.assertTrue(self.dispatcher.is_busy())
            self.assertFalse(self.dispatcher.submit("qr"))
            self.assertTrue(self.operation_lock.locked())
            self.assertEqual(self.activity, [])
            self.assertEqual(self.calls, [])
        finally:
            self.operation_lock.release()
        self.assert_idle()
        self.run_action("capture")
        self.assertEqual(self.calls, ["capture"])

    def test_async_display_busy_remains_effective_after_handler_returns(self):
        def starts_refresh():
            self.calls.append("qr")
            self.display_busy.set()
            return {"success": True}

        self.dispatcher = ButtonActionDispatcher(
            self.operation_lock, self.display_busy.is_set,
            {"capture": self.capture, "qr": starts_refresh}, self.update_activity,
        )
        self.assertTrue(self.dispatcher.submit("qr"))
        self.dispatcher.wait(2)
        self.assertFalse(self.operation_lock.locked())
        self.assertTrue(self.dispatcher.is_busy())
        self.assertFalse(self.dispatcher.submit("capture"))
        self.display_busy.clear()
        self.assert_idle()
        self.assertEqual(self.calls, ["qr"])
        self.assertEqual(self.activity, ["activity"])

    def test_unsuccessful_result_releases_lock_and_allows_next_action(self):
        def failed_qr():
            return {"success": False, "message": "No network available"}

        self.dispatcher = ButtonActionDispatcher(
            self.operation_lock, self.display_busy.is_set,
            {"capture": self.capture, "qr": failed_qr}, self.update_activity,
        )
        with self.assertLogs(level="WARNING") as captured:
            self.run_action("qr")
        self.assertTrue(any("No network available" in line for line in captured.output))
        self.run_action("capture")
        self.assertEqual(self.calls, ["capture"])
        self.assertEqual(self.activity, ["activity", "activity"])

    def test_handler_exception_releases_lock_and_allows_next_action(self):
        def failed_qr():
            raise RuntimeError("display failed")

        self.dispatcher = ButtonActionDispatcher(
            self.operation_lock, self.display_busy.is_set,
            {"capture": self.capture, "qr": failed_qr}, self.update_activity,
        )
        with self.assertLogs(level="ERROR") as captured:
            self.run_action("qr")
        self.assertTrue(any("display failed" in line for line in captured.output))
        self.run_action("capture")
        self.assertEqual(self.calls, ["capture"])

    def test_activity_exception_releases_lock_without_running_handler(self):
        def failed_activity():
            raise RuntimeError("activity failed")

        self.dispatcher = ButtonActionDispatcher(
            self.operation_lock, self.display_busy.is_set,
            {"capture": self.capture}, failed_activity,
        )
        with self.assertLogs(level="ERROR"):
            self.run_action("capture")
        self.assertEqual(self.calls, [])

    def test_display_status_exception_releases_lock(self):
        busy_check = unittest.mock.Mock(side_effect=RuntimeError("status failed"))
        self.dispatcher = ButtonActionDispatcher(
            self.operation_lock, busy_check,
            {"capture": self.capture}, self.update_activity,
        )
        with self.assertRaisesRegex(RuntimeError, "status failed"):
            self.dispatcher.submit("capture")
        self.assertFalse(self.operation_lock.locked())
        busy_check.side_effect = None
        busy_check.return_value = False
        self.assert_idle()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.activity, [])
        self.run_action("capture")

    def test_unknown_action_does_not_lock_or_update_activity(self):
        with self.assertRaisesRegex(ValueError, "Unknown button action"):
            self.dispatcher.submit("double")
        self.assert_idle()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.activity, [])
        self.run_action("capture")

    def test_thread_start_failure_releases_lock_and_allows_retry(self):
        with patch("button_actions.threading.Thread.start",
                   side_effect=RuntimeError("cannot start thread")):
            with self.assertRaisesRegex(RuntimeError, "cannot start thread"):
                self.dispatcher.submit("qr")
        self.assert_idle()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.activity, [])
        self.run_action("capture")
        self.assertEqual(self.calls, ["capture"])


if __name__ == "__main__":
    unittest.main()
