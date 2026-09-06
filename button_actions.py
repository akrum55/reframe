"""Austin's QR shortcut (2026-09-05): non-queued, non-blocking button actions.

Only gesture recognition runs on the polling thread. The same operation lock
as the HTTP API protects camera/display work. Busy presses are discarded, not
queued to take surprising photographs after a refresh finishes.
"""

import logging
import threading


class ButtonActionDispatcher:
    def __init__(self, operation_lock, display_busy, handlers, update_activity):
        self._operation_lock = operation_lock
        self._display_busy = display_busy
        self._handlers = handlers
        self._update_activity = update_activity
        self._active = threading.Event()
        self._thread = None

    def is_busy(self):
        return (self._active.is_set() or self._operation_lock.locked()
                or self._display_busy())

    def submit(self, action):
        if action not in self._handlers:
            raise ValueError("Unknown button action")
        if self._active.is_set() or not self._operation_lock.acquire(blocking=False):
            return False
        try:
            # Recheck under the API lock: another operation may have just
            # dispatched an asynchronous panel refresh.
            if self._display_busy():
                self._operation_lock.release()
                return False
            self._active.set()
            self._thread = threading.Thread(
                target=self._run, args=(action,), daemon=True,
                name="reframe-button-action")
            self._thread.start()
        except Exception:
            self._active.clear()
            self._operation_lock.release()
            raise
        return True

    def _run(self, action):
        try:
            self._update_activity()
            result = self._handlers[action]()
            if result.get("success"):
                logging.info("Button action completed: %s", action)
            else:
                logging.warning("Button action %s failed: %s", action,
                                result.get("message", result.get("error", "unknown")))
        except Exception:
            logging.exception("Button action failed: %s", action)
        finally:
            self._active.clear()
            self._operation_lock.release()

    def wait(self, timeout=None):
        """For controlled teardown/tests; never called by the polling loop."""
        if self._thread is not None:
            self._thread.join(timeout)
