"""Austin custom fork, September 5, 2026: debounced shutter/QR gestures.

This module has no Pi dependencies and never changes PiSugar's shutdown behavior.
The caller supplies raw readings and monotonic timestamps, normally every 25 ms.
"""

from __future__ import annotations


class ButtonGestureRecognizer:
    """Recognize one short tap (capture) or three short taps (dashboard QR).

    Call ``update(pressed, now)`` with True/False readings and ``time.monotonic()``;
    use None for a failed read. It returns "capture", "qr", or None. A tap must
    have debounced press and release edges. Two taps expire without an action.
    Holds of at least two seconds cancel pending taps; PiSugar retains shutdown.
    A failed read discards the whole burst until 500 ms of continuous release.

    Timing uses the first observed edge once that edge survives debounce. A next
    press observed exactly 500 ms after release belongs to the same sequence.
    A press candidate inside the window postpones capture until debounce settles.
    After QR, additional taps are ignored until 500 ms of continuously observed
    release. Startup, failed reads, and reset require a fresh stable release.
    """

    DEBOUNCE_SECONDS = 0.025
    INTER_TAP_SECONDS = 0.5
    LONG_PRESS_SECONDS = 2.0

    def __init__(self) -> None:
        self._suppressing = False
        self.reset()

    def reset(self) -> None:
        """Discard pending input and require a newly observed stable release.

        Call while another operation owns the camera/display to drop busy-time
        presses. An active QR/fault quiet-period guard survives reset: its 500 ms clock
        restarts with the next released reading. Thus a quickly failing QR action
        followed by a fourth tap cannot turn that tap into an accidental photo.
        """
        self._armed = False
        self._raw_pressed: bool | None = None
        self._raw_since: float | None = None
        self._stable_pressed: bool | None = None
        self._press_started_at: float | None = None
        self._tap_count = 0
        self._last_release_at: float | None = None

    def _finish_sequence(self) -> str | None:
        action = "capture" if self._tap_count == 1 else None
        self._tap_count = 0
        self._last_release_at = None
        return action

    def update(self, pressed: bool | None, now: float) -> str | None:
        """Consume one reading; ``now`` must be a monotonic time in seconds."""
        if pressed is None:
            # An I2C fault is unknown input, never an invented button release.
            # Discard trailing taps from the interrupted burst too: otherwise
            # the third tap could be mistaken for a new single-photo command.
            self._suppressing = True
            self.reset()
            return None

        if (self._suppressing and self._raw_pressed is False
                and self._stable_pressed is False
                and now >= self._raw_since + self.INTER_TAP_SECONDS):
            # A new press can be the first poll after the quiet deadline. The
            # preceding released interval still counts, just as for tap gaps.
            # Raw timing makes even a rejected bounce restart the quiet period.
            self._suppressing = False

        if pressed != self._raw_pressed:
            self._raw_pressed = pressed
            self._raw_since = now

        edge = False
        if (now >= self._raw_since + self.DEBOUNCE_SECONDS
                and self._stable_pressed != pressed):
            self._stable_pressed = pressed
            edge = True

        if not self._armed:
            if not pressed and self._stable_pressed is False:
                self._armed = True
            return None

        if self._suppressing:
            # Keep debouncing ignored input so the next quiet interval is known.
            return None

        action = None
        if edge and pressed:
            # A late next press starts a separate sequence. Usually polling has
            # already expired the previous one, but delayed polls must agree.
            if (self._last_release_at is not None
                    and self._raw_since > self._last_release_at + self.INTER_TAP_SECONDS):
                action = self._finish_sequence()
            self._press_started_at = self._raw_since
            self._last_release_at = None

        if self._press_started_at is not None:
            # A release just before two seconds is short even if confirmation is
            # later; a release at/after two seconds is always a canceled hold.
            hold_until = now if pressed else self._raw_since
            if hold_until >= self._press_started_at + self.LONG_PRESS_SECONDS:
                self.reset()
                return action

        if edge and not pressed and self._press_started_at is not None:
            self._press_started_at = None
            self._last_release_at = self._raw_since
            self._tap_count += 1
            if self._tap_count == 3:
                self._finish_sequence()
                self._suppressing = True
                return "qr"

        if (not pressed and self._stable_pressed is False
                and self._last_release_at is not None
                and now >= self._last_release_at + self.INTER_TAP_SECONDS):
            return self._finish_sequence()

        return action
