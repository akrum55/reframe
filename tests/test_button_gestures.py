"""Austin custom fork, September 5, 2026: hardware-free gesture regressions."""

import unittest

from button_gestures import ButtonGestureRecognizer


class ButtonGestureTests(unittest.TestCase):
    def setUp(self):
        self.button = ButtonGestureRecognizer()
        self.actions = []
        self.sample(False, 0.0)
        self.sample(False, 0.025)

    def sample(self, pressed, now):
        action = self.button.update(pressed, now)
        if action is not None:
            self.actions.append(action)
        return action

    def edge(self, pressed, now):
        self.sample(pressed, now)
        return self.sample(pressed, now + self.button.DEBOUNCE_SECONDS)

    def tap(self, press, release):
        self.edge(True, press)
        return self.edge(False, release)

    def test_single_waits_500_ms_after_release_and_fires_once(self):
        self.tap(0.1, 0.2)
        self.assertIsNone(self.sample(False, 0.699))
        self.assertEqual(self.sample(False, 0.7), "capture")
        self.sample(False, 10.0)
        self.assertEqual(self.actions, ["capture"])

    def test_triple_returns_qr_on_third_debounced_release(self):
        self.tap(0.1, 0.2)
        self.tap(0.3, 0.4)
        self.edge(True, 0.5)
        self.assertIsNone(self.sample(False, 0.6))
        self.assertEqual(self.sample(False, 0.625), "qr")
        self.sample(False, 2.0)
        self.assertEqual(self.actions, ["qr"])

    def test_double_expires_without_photo_or_qr(self):
        self.tap(0.1, 0.2)
        self.tap(0.3, 0.4)
        self.sample(False, 0.9)
        self.sample(False, 5.0)
        self.assertEqual(self.actions, [])

    def test_press_at_exact_intertap_boundary_continues_sequence(self):
        self.tap(0.1, 0.2)
        self.tap(0.2 + 0.5, 0.8)
        self.tap(1.0, 1.1)
        self.assertEqual(self.actions, ["qr"])

    def test_press_started_inside_window_can_debounce_after_deadline(self):
        self.tap(0.1, 0.2)
        self.tap(0.69, 0.8)
        self.sample(False, 1.3)
        self.assertEqual(self.actions, [])

    def test_press_after_window_is_a_new_single_even_if_poll_was_delayed(self):
        self.tap(0.1, 0.2)
        self.tap(0.701, 0.8)
        self.sample(False, 1.3)
        self.assertEqual(self.actions, ["capture", "capture"])

    def test_double_timeout_then_new_single(self):
        self.tap(0.1, 0.2)
        self.tap(0.3, 0.4)
        self.sample(False, 0.9)
        self.tap(1.0, 1.1)
        self.sample(False, 1.6)
        self.assertEqual(self.actions, ["capture"])

    def test_press_and_release_bounce_make_one_tap(self):
        for pressed, now in [(True, 0.1), (False, 0.11), (True, 0.12),
                             (True, 0.145), (False, 0.2), (True, 0.21),
                             (False, 0.22), (False, 0.245)]:
            self.sample(pressed, now)
        self.sample(False, 0.72)
        self.assertEqual(self.actions, ["capture"])

    def test_pulse_shorter_than_debounce_is_ignored(self):
        self.sample(True, 0.1)
        self.edge(False, 0.12)
        self.sample(False, 1.0)
        self.assertEqual(self.actions, [])

    def test_long_hold_cancels_and_requires_release_before_fresh_tap(self):
        self.edge(True, 0.1)
        self.sample(True, 2.1)
        self.sample(True, 3.0)
        self.edge(False, 3.1)
        self.sample(False, 4.0)
        self.assertEqual(self.actions, [])
        self.tap(4.1, 4.2)
        self.sample(False, 4.7)
        self.assertEqual(self.actions, ["capture"])

    def test_long_hold_after_one_tap_cancels_pending_photo(self):
        self.tap(0.1, 0.2)
        self.edge(True, 0.3)
        self.sample(True, 0.8)
        self.sample(True, 2.3)
        self.edge(False, 2.4)
        self.sample(False, 3.0)
        self.assertEqual(self.actions, [])

    def test_long_third_press_cancels_qr_sequence(self):
        self.tap(0.1, 0.2)
        self.tap(0.3, 0.4)
        self.edge(True, 0.5)
        self.sample(True, 2.5)
        self.edge(False, 2.6)
        self.sample(False, 3.5)
        self.assertEqual(self.actions, [])

    def test_release_at_exact_two_seconds_does_not_capture(self):
        self.tap(0.1, 2.1)
        self.sample(False, 3.0)
        self.assertEqual(self.actions, [])

    def test_release_before_two_seconds_is_short_despite_later_debounce(self):
        self.tap(0.1, 2.099)
        self.sample(False, 2.599)
        self.assertEqual(self.actions, ["capture"])

    def test_held_startup_is_ignored_until_stable_release(self):
        self.button = ButtonGestureRecognizer()
        self.edge(True, 0.0)
        self.sample(True, 3.0)
        self.edge(False, 3.1)
        self.sample(False, 3.7)
        self.assertEqual(self.actions, [])
        self.tap(3.8, 3.9)
        self.sample(False, 4.4)
        self.assertEqual(self.actions, ["capture"])

    def test_read_fault_during_press_does_not_manufacture_release(self):
        self.edge(True, 0.1)
        self.sample(None, 0.2)
        self.edge(True, 0.3)
        self.edge(False, 0.4)
        self.sample(False, 1.0)
        self.assertEqual(self.actions, [])
        self.tap(1.1, 1.2)
        self.sample(False, 1.7)
        self.assertEqual(self.actions, ["capture"])

    def test_read_fault_cancels_pending_single(self):
        self.tap(0.1, 0.2)
        self.sample(None, 0.3)
        self.edge(False, 0.4)
        self.sample(False, 1.0)
        self.assertEqual(self.actions, [])

    def test_fault_recovery_needs_stable_release(self):
        self.sample(None, 0.1)
        self.sample(False, 0.2)
        self.edge(True, 0.21)
        self.edge(False, 0.4)
        self.sample(False, 1.0)
        self.assertEqual(self.actions, [])

    def test_fault_in_triple_does_not_turn_final_tap_into_photo(self):
        self.tap(0.1, 0.2)
        self.tap(0.3, 0.4)
        self.sample(None, 0.45)
        self.edge(False, 0.46)
        self.tap(0.55, 0.65)
        self.sample(False, 1.2)
        self.assertEqual(self.actions, [])
        self.tap(1.3, 1.4)
        self.sample(False, 1.9)
        self.assertEqual(self.actions, ["capture"])

    def test_fault_quiet_deadline_allows_fresh_press_without_extra_poll(self):
        self.sample(None, 0.1)
        self.edge(False, 0.125)
        self.tap(0.625, 0.75)
        self.sample(False, 1.25)
        self.assertEqual(self.actions, ["capture"])

    def test_repeated_fault_restarts_released_quiet_period(self):
        self.sample(None, 0.1)
        self.edge(False, 0.2)
        self.sample(None, 0.6)
        self.edge(False, 0.61)
        self.tap(0.9, 1.0)
        self.sample(False, 1.5)
        self.assertEqual(self.actions, [])
        self.tap(1.6, 1.7)
        self.sample(False, 2.2)
        self.assertEqual(self.actions, ["capture"])

    def test_busy_reset_preserves_fault_quiet_guard(self):
        self.sample(None, 0.2)
        self.edge(False, 0.3)
        self.button.reset()
        self.edge(False, 0.4)
        self.tap(0.5, 0.6)
        self.sample(False, 1.1)
        self.assertEqual(self.actions, [])
        self.tap(1.2, 1.3)
        self.sample(False, 1.8)
        self.assertEqual(self.actions, ["capture"])

    def test_reset_during_press_discards_later_release(self):
        self.edge(True, 0.1)
        self.button.reset()
        self.edge(False, 0.2)
        self.sample(False, 1.0)
        self.assertEqual(self.actions, [])

    def test_reset_discards_pending_single(self):
        self.tap(0.1, 0.2)
        self.button.reset()
        self.edge(False, 0.3)
        self.sample(False, 1.0)
        self.assertEqual(self.actions, [])

    def test_four_rapid_taps_only_show_qr_then_quiet_allows_fresh_capture(self):
        for start in [0.1, 0.3, 0.5, 0.7]:
            self.tap(start, start + 0.1)
        self.sample(False, 1.3)
        self.assertEqual(self.actions, ["qr"])
        self.tap(1.4, 1.5)
        self.sample(False, 2.0)
        self.assertEqual(self.actions, ["qr", "capture"])

    def test_seven_rapid_taps_only_show_one_qr(self):
        for start in [0.1, 0.3, 0.5, 0.7, 0.9, 1.1, 1.3]:
            self.tap(start, start + 0.1)
        self.sample(False, 3.0)
        self.assertEqual(self.actions, ["qr"])

    def test_new_press_after_qr_quiet_deadline_does_not_need_extra_idle_poll(self):
        self.tap(0.1, 0.2)
        self.tap(0.3, 0.4)
        self.tap(0.5, 0.6)
        self.tap(1.2, 1.3)
        self.sample(False, 1.8)
        self.assertEqual(self.actions, ["qr", "capture"])

    def test_new_press_at_exact_qr_quiet_deadline_is_a_fresh_tap(self):
        self.tap(0.1, 0.2)
        self.tap(0.3, 0.4)
        self.tap(0.5, 0.6)
        self.tap(0.6 + 0.5, 1.2)
        self.sample(False, 1.7)
        self.assertEqual(self.actions, ["qr", "capture"])

    def test_even_a_bounce_restarts_post_qr_quiet_time(self):
        self.tap(0.1, 0.2)
        self.tap(0.3, 0.4)
        self.tap(0.5, 0.6)
        self.sample(True, 1.0)
        self.sample(False, 1.01)
        self.sample(False, 1.1)
        self.tap(1.2, 1.3)
        self.sample(False, 1.8)
        self.assertEqual(self.actions, ["qr"])

    def test_busy_reset_preserves_qr_guard_against_fourth_tap(self):
        self.tap(0.1, 0.2)
        self.tap(0.3, 0.4)
        self.tap(0.5, 0.6)
        self.button.reset()
        self.edge(False, 0.65)
        self.tap(0.7, 0.8)
        self.sample(False, 1.3)
        self.assertEqual(self.actions, ["qr"])
        self.tap(1.4, 1.5)
        self.sample(False, 2.0)
        self.assertEqual(self.actions, ["qr", "capture"])


if __name__ == "__main__":
    unittest.main()
