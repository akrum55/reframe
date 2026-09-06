"""Hardware-free bounded-retry and gesture-integration regressions."""

import errno
import unittest
from unittest.mock import Mock

from button_gestures import ButtonGestureRecognizer
from button_input import MAX_READ_ATTEMPTS, RETRY_DELAY_SECONDS, read_button_state


class ButtonInputTests(unittest.TestCase):
    def read(self, outcomes):
        reader = Mock(side_effect=outcomes)
        sleeper = Mock()
        with self.assertLogs(level="INFO") if len(outcomes) > 1 or isinstance(outcomes[0], Exception) else self.no_logs():
            value = read_button_state(reader, retry_sleep=sleeper)
        return value, reader, sleeper

    def no_logs(self):
        from contextlib import nullcontext
        return nullcontext()

    def test_success_does_not_sleep_or_retry(self):
        value, reader, sleeper = self.read([1])
        self.assertIs(value, True)
        self.assertEqual(reader.call_count, 1)
        sleeper.assert_not_called()

    def test_only_bit_zero_indicates_pressed(self):
        for register in (0, 2, 0xFE):
            with self.subTest(register=register):
                value, reader, sleeper = self.read([register])
                self.assertIs(value, False)
        self.assertIs(self.read([0xFF])[0], True)

    def test_remote_io_error_recovers_pressed(self):
        value, reader, sleeper = self.read([OSError(121, "transient"), 1])
        self.assertIs(value, True)
        self.assertEqual(reader.call_count, 2)
        sleeper.assert_called_once_with(RETRY_DELAY_SECONDS)

    def test_io_error_recovers_released(self):
        value, reader, sleeper = self.read([OSError(errno.EIO, "transient"), 0])
        self.assertIs(value, False)
        self.assertEqual(reader.call_count, 2)

    def test_last_allowed_attempt_can_succeed(self):
        value, reader, sleeper = self.read([OSError(121, "transient")] * 3 + [1])
        self.assertIs(value, True)
        self.assertEqual(reader.call_count, MAX_READ_ATTEMPTS)
        self.assertEqual(sleeper.call_count, MAX_READ_ATTEMPTS - 1)

    def test_mixed_transient_errors_recover_on_third_attempt(self):
        value, reader, sleeper = self.read([OSError(121, "transient"), OSError(errno.EIO, "transient"), 1])
        self.assertIs(value, True)
        self.assertEqual(reader.call_count, 3)
        self.assertEqual(sleeper.call_count, 2)

    def test_exhausted_reads_are_unknown_not_release(self):
        value, reader, sleeper = self.read([OSError(121, "persistent")] * 4)
        self.assertIsNone(value)
        self.assertEqual(reader.call_count, 4)
        self.assertEqual(sleeper.call_count, 3)
        self.assertAlmostEqual(sum(c.args[0] for c in sleeper.call_args_list), 0.03)

    def test_unexpected_os_error_is_not_retried(self):
        value, reader, sleeper = self.read([OSError(errno.ENODEV, "unavailable")])
        self.assertIsNone(value)
        self.assertEqual(reader.call_count, 1)
        sleeper.assert_not_called()

    def test_unexpected_exception_is_not_retried(self):
        value, reader, sleeper = self.read([RuntimeError("unknown")])
        self.assertIsNone(value)
        self.assertEqual(reader.call_count, 1)
        sleeper.assert_not_called()

    def test_unknown_error_after_transient_stops_retrying(self):
        value, reader, sleeper = self.read([OSError(121, "transient"), OSError(errno.EINVAL, "invalid")])
        self.assertIsNone(value)
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(sleeper.call_count, 1)

    def test_process_interrupts_are_not_swallowed(self):
        for error in (KeyboardInterrupt(), SystemExit()):
            reader = Mock(side_effect=error)
            sleeper = Mock()
            with self.assertRaises(type(error)):
                read_button_state(reader, retry_sleep=sleeper)
            self.assertEqual(reader.call_count, 1)
            sleeper.assert_not_called()

    def test_recovery_does_not_reuse_previous_pressed_value(self):
        reader = Mock(side_effect=[1, OSError(121, "transient"), 0])
        self.assertIs(read_button_state(reader, retry_sleep=Mock()), True)
        with self.assertLogs(level="INFO"):
            self.assertIs(read_button_state(reader, retry_sleep=Mock()), False)

    def test_transient_error_on_each_press_can_still_recognize_triple(self):
        gesture = ButtonGestureRecognizer()
        actions = []
        for pressed, now in ((False, 0), (False, 0.03),
                             (True, 0.1), (True, 0.14), (False, 0.3), (False, 0.34),
                             (True, 0.6), (True, 0.64), (False, 0.8), (False, 0.84),
                             (True, 1.1), (True, 1.14), (False, 1.3), (False, 1.34)):
            outcomes = [OSError(121, "transient"), 1] if pressed else [0]
            value, _, _ = self.read(outcomes)
            # Include retry latency; do not backdate a recovered sample.
            sampled_at = now + (RETRY_DELAY_SECONDS if pressed else 0)
            action = gesture.update(value, sampled_at)
            if action:
                actions.append(action)
        self.assertEqual(actions, ["qr"])

    def test_exhausted_retries_keep_full_burst_fault_guard(self):
        gesture = ButtonGestureRecognizer()
        for state, now in ((False, 0), (False, 0.03), (True, 0.1),
                           (True, 0.13), (False, 0.2), (False, 0.23)):
            self.assertIsNone(gesture.update(state, now))
        failed, _, _ = self.read([OSError(121, "persistent")] * 4)
        self.assertIsNone(gesture.update(failed, 0.3))
        actions = []
        for state, now in ((False, 0.4), (False, 0.43), (True, 0.5),
                           (True, 0.53), (False, 0.6), (False, 0.63), (False, 2)):
            action = gesture.update(state, now)
            if action:
                actions.append(action)
        self.assertEqual(actions, [])

    def test_recovered_release_still_requires_debounce(self):
        gesture = ButtonGestureRecognizer()
        for state, now in ((False, 0), (False, 0.03), (True, 0.1), (True, 0.14)):
            self.assertIsNone(gesture.update(state, now))
        released, _, _ = self.read([OSError(121, "transient"), 0])
        self.assertIsNone(gesture.update(released, 0.21))
        self.assertIsNone(gesture.update(True, 0.22))  # release shorter than debounce
        self.assertIsNone(gesture.update(True, 0.25))
        self.assertIsNone(gesture.update(False, 0.4))
        self.assertIsNone(gesture.update(False, 0.44))
        self.assertEqual(gesture.update(False, 0.91), "capture")

    def test_retry_latency_does_not_extend_gap(self):
        gesture = ButtonGestureRecognizer()
        for state, now in ((False, 0), (False, 0.03), (True, 0.1),
                           (True, 0.14), (False, 0.2), (False, 0.24)):
            self.assertIsNone(gesture.update(state, now))
        recovered, _, _ = self.read([OSError(121, "transient")] * 3 + [1])
        # Reading began inside the limit, but the actual recovered sample is late.
        self.assertIsNone(gesture.update(recovered, 0.69 + 0.03))
        self.assertEqual(gesture.update(True, 0.76), "capture")


if __name__ == "__main__":
    unittest.main()
