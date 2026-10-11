import errno
import unittest
from unittest.mock import Mock

from button_health import ButtonHealthMonitor
from button_input import read_button_state


class ButtonHealthTests(unittest.TestCase):
    def test_valid_press_and_release_are_unchanged(self):
        report = Mock()
        health = ButtonHealthMonitor(report)
        self.assertIs(health.observe(True), True)
        self.assertIs(health.observe(False), False)
        report.assert_not_called()

    def test_unknown_remains_unknown_and_reports_once(self):
        report = Mock()
        health = ButtonHealthMonitor(report)
        for _ in range(10):
            self.assertIsNone(health.observe(None))
        self.assertEqual(report.call_count, 1)
        self.assertEqual(report.call_args.args[0], 'button')

    def test_recovery_reports_once_and_keeps_real_state(self):
        report = Mock()
        health = ButtonHealthMonitor(report)
        health.observe(None)
        self.assertIs(health.observe(True), True)
        self.assertIs(health.observe(False), False)
        self.assertEqual(report.call_count, 2)
        report.assert_called_with('button', activity='Shutter button connection recovered')

    def test_recovered_retry_is_not_a_fault(self):
        report = Mock()
        health = ButtonHealthMonitor(report)
        reader = Mock(side_effect=[OSError(errno.EIO, 'fixture'), 1])
        self.assertIs(health.observe(read_button_state(reader, retry_sleep=lambda _: None)), True)
        self.assertEqual(reader.call_count, 2)
        report.assert_not_called()

    def test_exhausted_reads_do_not_synthesize_release(self):
        report = Mock()
        health = ButtonHealthMonitor(report)
        health.observe(True)
        reader = Mock(side_effect=OSError(errno.EIO, 'fixture'))
        self.assertIsNone(health.observe(read_button_state(reader, retry_sleep=lambda _: None)))
        self.assertEqual(reader.call_count, 4)
        report.assert_called_once()
