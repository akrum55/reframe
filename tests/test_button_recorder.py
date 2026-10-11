"""Bounded opt-in recorder tests; no device/network/photo data or hardware calls."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from button_gestures import ButtonGestureRecognizer
from button_trace import ButtonTrace, load_trace, ARM_NAME, OUTPUT_NAME


class EventRecorderTests(unittest.TestCase):
    def recorder(self, **kwargs):
        return ButtonTrace(10, clock=lambda: 0, **kwargs)

    def observe(self, trace, gesture, raw, now, **kwargs):
        action = gesture.update(raw, now)
        trace.observe(raw=raw, now=now, read_seconds=0.001, busy=False,
                      gesture=gesture, emitted=action, **kwargs)
        return action

    def test_trace_preserves_single_triple_fault_and_hold_outcomes(self):
        for ranges, faults in ((((0.2, 0.3),), ()),
                               (((0.2, 0.3), (0.4, 0.5), (0.6, 0.7)), ()),
                               (((0.2, 0.3), (0.4, 0.5), (0.6, 0.7)), (18,)),
                               (((0.2, 2.5),), ())):
            plain, observed = ButtonGestureRecognizer(), ButtonGestureRecognizer()
            trace = self.recorder()
            expected, actual = [], []
            for index in range(140):
                now = index * 0.025
                raw = None if index in faults else any(start <= now < end for start, end in ranges)
                expected.append(plain.update(raw, now))
                action = self.observe(trace, observed, raw, now)
                actual.append(action)
                if action:
                    trace.action(now=now, emitted=action, submitted=True)
                self.assertEqual(vars(plain), vars(observed))
            self.assertEqual(expected, actual)
        self.assertEqual(ButtonGestureRecognizer.INTER_TAP_SECONDS, 0.5)

    def test_raw_debounce_busy_and_dispatch_are_distinct_records(self):
        gesture = ButtonGestureRecognizer()
        trace = self.recorder()
        self.observe(trace, gesture, False, 0)
        self.observe(trace, gesture, False, 0.03)
        self.observe(trace, gesture, True, 0.1)
        self.observe(trace, gesture, True, 0.13)
        gesture.reset()
        trace.observe(raw=None, now=0.2, read_seconds=None, busy=True,
                      gesture=gesture, sampled=False)
        trace.action(now=0.2, emitted='qr', submitted=False)
        events = trace.snapshot()['events']
        self.assertEqual(events[2]['raw'], True)
        self.assertEqual(events[2]['state']['stable'], False)
        self.assertEqual(events[3]['state']['stable'], True)
        self.assertFalse(events[4]['sampled'])
        self.assertTrue(events[4]['busy'])
        self.assertEqual(events[5]['kind'], 'dispatch')
        self.assertFalse(events[5]['submitted'])

    def test_read_delays_loop_gaps_and_dropped_events_are_explicit(self):
        gesture, trace = ButtonGestureRecognizer(), self.recorder()
        self.observe(trace, gesture, False, 0)
        self.observe(trace, gesture, False, 0.03)
        trace.observe(raw=False, now=0.2, read_seconds=0.011, busy=False, gesture=gesture)
        event = trace.snapshot()['events'][-1]
        self.assertEqual(event['read_ms'], 11)
        self.assertEqual(event['loop_ms'], 170)
        trace._lock.acquire()
        try:
            self.observe(trace, gesture, True, 0.23)
            trace.action(now=0.23, emitted='capture', submitted=True)
        finally:
            trace._lock.release()
        self.assertEqual(trace.snapshot()['dropped_lock'], 2)

    def test_observation_has_no_file_logging_sleep_or_hardware_io(self):
        trace, gesture = self.recorder(), ButtonGestureRecognizer()
        with patch('builtins.open', side_effect=AssertionError('No open')), \
             patch('os.open', side_effect=AssertionError('No os.open')), \
             patch('time.sleep', side_effect=AssertionError('No sleep')), \
             patch('logging.info', side_effect=AssertionError('No log')), \
             patch('button_input.read_button_state', side_effect=AssertionError('No extra read')):
            self.observe(trace, gesture, False, 0)
            self.observe(trace, gesture, True, 0.1)
            trace.action(now=0.1, emitted='capture', submitted=True)
        self.assertEqual(trace.snapshot()['recorder_faults'], 0)
        self.assertEqual(trace.snapshot()['events_total'], 3)

    def test_capacity_and_duration_are_hard_bounds(self):
        trace, gesture = self.recorder(capacity=3), ButtonGestureRecognizer()
        for index in range(10):
            self.observe(trace, gesture, bool(index % 2), index * 0.1)
        snapshot = trace.snapshot()
        self.assertEqual(len(snapshot['events']), 3)
        self.assertEqual(snapshot['events_total'], 10)
        self.assertEqual(snapshot['overwritten'], 7)
        self.observe(trace, gesture, False, 10)
        trace.action(now=10, emitted='qr', submitted=True)
        self.assertEqual(trace.snapshot()['events_total'], 10)
        self.assertFalse(trace.snapshot()['active'])

    def test_bad_observations_never_escape_or_record_arbitrary_strings(self):
        class BrokenGesture:
            @property
            def _armed(self):
                raise RuntimeError('private-fixture')
        trace = self.recorder()
        trace.observe(raw=True, now=0.1, read_seconds=0, busy=False, gesture=BrokenGesture())
        trace.observe(raw='private-fixture', now=0.2, read_seconds='private-fixture',
                      busy='private-fixture', gesture=object(), emitted='private-fixture')
        trace.action(now=0.3, emitted='private-fixture', submitted=True)
        snapshot = trace.snapshot()
        self.assertGreater(snapshot['recorder_faults'], 0)
        self.assertNotIn('private-fixture', json.dumps(snapshot))

    def test_writer_waits_for_quiet_and_publishes_only_changed_or_final(self):
        now = [0.0]
        writes = []
        trace = ButtonTrace(8, clock=lambda: now[0], writer=lambda value: writes.append((now[0], value)))
        gesture = ButtonGestureRecognizer()
        def wait(_):
            now[0] += 1
            if now[0] < 3:
                self.observe(trace, gesture, True, now[0])
            elif now[0] < 8:
                self.observe(trace, gesture, False, now[0])
            return now[0] > 8
        trace._stop.wait = wait
        trace._run()
        # State settles at t=4; not during press, release debounce, or tap expiry.
        self.assertEqual([stamp for stamp, _ in writes], [0, 7, 8])
        self.assertFalse(writes[-1][1]['active'])

    def test_worker_io_failure_never_escapes(self):
        trace = self.recorder(writer=Mock(side_effect=OSError('private-fixture')))
        trace._run()
        self.assertFalse(trace.snapshot()['active'])
        self.assertEqual(trace.snapshot()['recorder_faults'], 1)
        trace.close()


class TraceArmingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.arm = self.folder / ARM_NAME

    def tearDown(self):
        self.temp.cleanup()

    def request(self, **override):
        value = dict(schema_version=1, duration_seconds=600, created_at=100, expires_at=700)
        value.update(override)
        self.arm.write_text(json.dumps(value))

    def test_absent_marker_is_default_noop(self):
        with patch('threading.Thread') as thread:
            self.assertIsNone(load_trace(self.folder))
            thread.assert_not_called()
        self.assertFalse((self.folder / OUTPUT_NAME).exists())

    def test_valid_marker_is_consumed_once_and_expiry_bounds_duration(self):
        self.request()
        with patch('button_trace.time.time', return_value=150), patch.object(ButtonTrace, 'start') as start:
            trace = load_trace(self.folder)
            self.assertIsInstance(trace, ButtonTrace)
            self.assertEqual(trace.snapshot()['duration_seconds'], 550)
            self.assertFalse(self.arm.exists())
            self.assertIsNone(load_trace(self.folder))
            start.assert_called_once()

    def test_invalid_or_expired_marker_is_consumed_without_starting(self):
        for override in ({'expires_at': 99}, {'created_at': 200}, {'duration_seconds': 601},
                         {'duration_seconds': True}, {'schema_version': 2}, {'expires_at': float('inf')}):
            self.request(**override)
            with patch('button_trace.time.time', return_value=150), patch.object(ButtonTrace, 'start') as start:
                self.assertIsNone(load_trace(self.folder))
                self.assertFalse(self.arm.exists())
                start.assert_not_called()
        self.arm.write_text('not JSON')
        self.assertIsNone(load_trace(self.folder))
        self.assertFalse(self.arm.exists())

    def test_symlink_and_fifo_markers_cannot_read_target_or_block(self):
        target = self.folder / 'private-fixture'
        target.write_text('unchanged')
        self.arm.symlink_to(target)
        self.assertIsNone(load_trace(self.folder))
        self.assertEqual(target.read_text(), 'unchanged')
        self.assertFalse(self.arm.is_symlink())
        os.mkfifo(self.arm)
        self.assertIsNone(load_trace(self.folder))
        self.assertFalse(self.arm.exists())


if __name__ == "__main__":
    unittest.main()
