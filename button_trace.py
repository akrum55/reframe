"""One-shot, bounded, opt-in shutter diagnostics; no hardware/control decisions.

Startup consumes a private arm request. The polling path only appends sanitized
state changes to memory using a nonblocking lock. A separate daemon publishes
after three seconds of released/quiet input, never in response to a hot-loop
write. Importing this module or an absent arm request starts nothing.
"""
from __future__ import annotations

from collections import deque
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import threading
import time

MAX_DURATION_SECONDS = 600
MAX_EVENTS = 2048
QUIET_SECONDS = 3.0
ARM_NAME = 'button-trace-arm.json'
OUTPUT_NAME = 'button-trace.json'
_ACTIONS = ('capture', 'qr')


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


class ButtonTrace:
    """Pure numeric/state recorder; all public observation methods fail closed.

    ``now`` is the exact existing monotonic timestamp supplied to the recognizer.
    A skipped hardware read is ``sampled=False`` (not a fabricated raw release).
    ``action`` records the dispatcher result, never submits an action itself.
    """

    def __init__(self, duration_seconds, *, clock=time.monotonic,
                 capacity=MAX_EVENTS, writer=None):
        if not _number(duration_seconds) or not 0 < duration_seconds <= MAX_DURATION_SECONDS:
            raise ValueError('Invalid trace duration')
        if type(capacity) is not int or not 1 <= capacity <= MAX_EVENTS:
            raise ValueError('Invalid trace capacity')
        self._clock = clock
        self._start = clock()
        self._deadline = self._start + duration_seconds
        self._duration = duration_seconds
        self._events = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._closed = False
        self._last_poll = None
        self._last_state = None
        self._last_signature = None
        self._last_activity = self._start
        self._input_released = False
        self._total = self._dropped = self._overwritten = self._faults = 0
        self._writer = writer
        self._thread = None

    def _ms(self, stamp):
        return round((stamp - self._start) * 1000, 3) if _number(stamp) else None

    def _state(self, gesture):
        result = {}
        for name, attribute in (('armed', '_armed'), ('suppressing', '_suppressing'),
                                ('raw', '_raw_pressed'), ('stable', '_stable_pressed')):
            value = getattr(gesture, attribute, None)
            result[name] = value if type(value) is bool else None
        value = getattr(gesture, '_tap_count', None)
        result['taps'] = value if type(value) is int and 0 <= value <= 3 else None
        for name, attribute in (('raw_edge_ms', '_raw_since'),
                                ('press_start_ms', '_press_started_at'),
                                ('last_release_ms', '_last_release_at')):
            result[name] = self._ms(getattr(gesture, attribute, None))
        return result

    def _append(self, event):
        self._overwritten += int(len(self._events) == self._events.maxlen)
        self._total += 1
        event['seq'] = self._total
        self._events.append(event)

    def observe(self, *, raw, now, read_seconds, busy, gesture, emitted=None, sampled=True):
        """Record an already-read sample; never perform I/O, sleep, or block."""
        try:
            if self._closed:
                return
            if not self._lock.acquire(blocking=False):
                self._dropped += 1
                return
            try:
                if not _number(now) or now < self._start:
                    self._faults += 1
                    return
                if now >= self._deadline:
                    self._closed = True
                    return
                raw = raw if type(raw) is bool else None
                busy = busy if type(busy) is bool else None
                sampled = sampled is True
                state = self._state(gesture)
                signature = (raw, sampled, busy, tuple(state.items()))
                changed = signature != self._last_signature
                gap = None if self._last_poll is None else max(0, now - self._last_poll)
                self._last_poll = now
                self._input_released = sampled and raw is False
                if changed or not self._input_released or emitted in _ACTIONS:
                    self._last_activity = now
                read_ms = round(read_seconds * 1000, 3) if _number(read_seconds) and read_seconds >= 0 else None
                gap_ms = round(gap * 1000, 3) if gap is not None else None
                if changed or emitted in _ACTIONS or (gap_ms is not None and gap_ms >= 75) or (read_ms is not None and read_ms >= 8):
                    self._append({'kind': 'sample', 't_ms': self._ms(now),
                                  'raw': raw, 'sampled': sampled, 'busy': busy,
                                  'read_ms': read_ms, 'loop_ms': gap_ms,
                                  'before': self._last_state, 'state': state,
                                  'emitted': emitted if emitted in _ACTIONS else None})
                self._last_state = state
                self._last_signature = signature
            finally:
                self._lock.release()
        except Exception:
            self._faults += 1

    def action(self, *, now, emitted, submitted):
        """Observe an existing dispatch result without influencing the result."""
        try:
            if self._closed or emitted not in _ACTIONS or not _number(now):
                return
            if not self._lock.acquire(blocking=False):
                self._dropped += 1
                return
            try:
                if not self._start <= now < self._deadline:
                    return
                self._last_activity = now
                self._append({'kind': 'dispatch', 't_ms': self._ms(now),
                              'emitted': emitted,
                              'submitted': submitted if type(submitted) is bool else None})
            finally:
                self._lock.release()
        except Exception:
            self._faults += 1

    def snapshot(self, now=None):
        """Worker/test-only copy. Do not call from the polling path."""
        if now is None:
            now = self._clock()
        with self._lock:
            return {'schema_version': 1, 'active': not self._closed and now < self._deadline,
                    'start_monotonic': self._start, 'deadline_monotonic': self._deadline,
                    'elapsed_ms': self._ms(now), 'duration_seconds': self._duration,
                    'capacity': self._events.maxlen, 'events_total': self._total,
                    'dropped_lock': self._dropped, 'overwritten': self._overwritten,
                    'recorder_faults': self._faults, 'events': list(self._events)}

    def _run(self):
        try:
            initial = self.snapshot()
            self._writer(initial)  # Initial ready receipt, before the user test.
            published = (initial['events_total'], initial['dropped_lock'], initial['recorder_faults'])
            while not self._stop.wait(0.5):
                now = self._clock()
                with self._lock:
                    finished = self._closed or now >= self._deadline
                    quiet = self._input_released and now - self._last_activity >= QUIET_SECONDS
                    changed = (self._total, self._dropped, self._faults) != published
                    if finished:
                        self._closed = True
                if quiet and (changed or finished):
                    snapshot = self.snapshot(now)
                    self._writer(snapshot)
                    published = (snapshot['events_total'], snapshot['dropped_lock'], snapshot['recorder_faults'])
                if finished:
                    # Never force disk I/O into a held button/burst at expiry.
                    # A previous receipt self-expires via deadline_monotonic.
                    return
        except Exception:
            self._faults += 1
            self._closed = True  # Diagnostic failure never reaches the button loop.

    def start(self):
        try:
            if self._writer is not None and self._thread is None:
                self._thread = threading.Thread(target=self._run, name='button-trace-writer', daemon=True)
                self._thread.start()
        except Exception:
            self._faults += 1
            self._closed = True

    def close(self):
        """Nonblocking shutdown; never force a write or wait in the button loop."""
        try:
            self._closed = True
            self._stop.set()
        except Exception:
            pass


def _write_snapshot(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.button-trace-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, separators=(',', ':'), allow_nan=False)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def load_trace(runtime_path):
    """Consume one safe, current arm request at startup, or return None.

    Request: schema_version=1, duration_seconds in (0,600], created_at and
    expires_at epoch seconds. Expiry is both the arming and recording deadline.
    No request content is copied to the numeric/state-only output.
    """
    claimed = None
    try:
        runtime = Path(runtime_path)
        if not runtime.is_dir() or runtime.is_symlink():
            return None
        arm = runtime / ARM_NAME
        if not arm.exists() and not arm.is_symlink():
            return None
        fd, name = tempfile.mkstemp(prefix='.button-trace-claim-', dir=runtime)
        os.close(fd)
        claimed = Path(name)
        os.replace(arm, claimed)  # Atomically consume before parsing or starting.
        if not stat.S_ISREG(claimed.lstat().st_mode):
            return None
        fd = os.open(claimed, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 or info.st_uid != os.geteuid():
                return None
            record = json.loads(stream.read(1025))
        if not isinstance(record, dict) or record.get('schema_version') != 1:
            return None
        duration, created, expires = (record.get(key) for key in
                                      ('duration_seconds', 'created_at', 'expires_at'))
        now = time.time()
        if not all(_number(value) for value in (duration, created, expires)):
            return None
        if not (0 < duration <= MAX_DURATION_SECONDS and created <= now < expires <= created + MAX_DURATION_SECONDS):
            return None
        output = runtime / OUTPUT_NAME
        trace = ButtonTrace(min(duration, expires - now), writer=lambda value: _write_snapshot(output, value))
        trace.start()
        return trace
    except Exception:
        return None
    finally:
        if claimed is not None:
            try:
                claimed.unlink()
            except OSError:
                pass
