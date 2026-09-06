"""Synthetic replay: short presses, 700 ms gaps, one transient NACK per press.

No personal identifiers or device data are needed to reproduce this timing case.
The alternate window here is a test parameter, not a live setting change.
"""

import unittest
from unittest.mock import Mock, patch

from button_gestures import ButtonGestureRecognizer
from button_input import read_button_state


def replay(window):
    gesture = ButtonGestureRecognizer()
    gesture.INTER_TAP_SECONDS = window
    actions = []
    previous = False
    intervals = ((1.0, 1.2), (1.9, 2.1), (2.8, 3.0))
    with patch("button_input.logging"):
        for sample in range(201):
            now = sample * 0.025
            pressed = any(begin <= now < end for begin, end in intervals)
            outcomes = [int(pressed)]
            if pressed and not previous:
                outcomes.insert(0, OSError(121, "transient"))
            elapsed = []
            value = read_button_state(Mock(side_effect=outcomes), retry_sleep=elapsed.append)
            action = gesture.update(value, now + sum(elapsed))
            if action:
                actions.append(action)
            previous = pressed
    return actions


class ButtonTraceTests(unittest.TestCase):
    def test_retries_alone_do_not_join_700_ms_gaps_in_500_ms_window(self):
        self.assertEqual(replay(0.5), ["capture", "capture", "capture"])

    def test_retries_and_900_ms_window_recognize_same_pace_without_photos(self):
        self.assertEqual(replay(0.9), ["qr"])


if __name__ == "__main__":
    unittest.main()
