"""Read-only PiSugar button sampling with bounded transient-error recovery.

Only successful reads supply a button state. Retrying never writes a register,
changes power policy, or invents a press/release across an unknown interval.
"""

import errno
import logging
import time


MAX_READ_ATTEMPTS = 4
RETRY_DELAY_SECONDS = 0.010
_TRANSIENT_ERRORS = {errno.EIO, getattr(errno, "EREMOTEIO", 121)}


def read_button_state(read_register, *, retry_sleep=time.sleep):
    """Return the latest successfully read bit zero, or None on failure.

    ``read_register`` performs the same single-byte read on every attempt.
    Four total attempts add at most 30 ms deliberate waiting, plus I/O latency.
    Callers must timestamp the returned sample AFTER this function completes.
    Persistent or unexpected failures remain unknown input for the recognizer.
    """
    for attempt in range(1, MAX_READ_ATTEMPTS + 1):
        try:
            value = read_register()
        except OSError as error:
            if error.errno not in _TRANSIENT_ERRORS or attempt == MAX_READ_ATTEMPTS:
                logging.error("Button I2C read failed after %d attempt(s): %s", attempt, error)
                return None
            retry_sleep(RETRY_DELAY_SECONDS)
        except Exception as error:
            logging.error("Button I2C read failed without retry: %s", error)
            return None
        else:
            if attempt > 1:
                logging.info("Button I2C read recovered after %d attempts", attempt)
            return bool(value & 0x01)
