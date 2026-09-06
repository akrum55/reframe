# Triple-short-press QR customization

This is a custom feature, not stock upstream reFrame behavior.
Original source: [kaloyaan/reframe](https://github.com/kaloyaan/reframe).
Initial implementation base: `5b88b443a9225b7954b57bbb784854c081c6991b`.

## Controls while powered on and ready

| Action | Result |
| --- | --- |
| One short press, then a 0.5-second pause | Capture one photo |
| Three short presses, release-to-next-press gaps at most 0.5 seconds | Show dashboard QR without a photo |
| Two short presses | No action |
| Hold for at least two seconds | Cancel taps; retain existing PiSugar shutdown |
| Extra rapid taps after QR | Ignored until a continuous 0.5-second release |
| Input during capture, display refresh, or a hardware API operation | Discarded, not queued |
| Transient button communication error | Retry briefly; use only a successfully read state |
| Persistent/unexpected button communication error | Discard the interrupted burst until 0.5 seconds of continuous release |

Automatic QR on Wi-Fi connection/reconnection defaults to off. Existing saved
settings must explicitly be migrated to off; defaults do not override saved true.
The manual dashboard QR control and explicit automatic-QR opt-in remain available.
The existing startup photo is unchanged. Without a usable LAN address, QR fails
without a photo or screen change.

No PiSugar firmware, wiring, shutdown configuration, service unit, dependency, or
camera capture/processing algorithm is changed by the feature.

## Code boundaries

- `button_gestures.py`: hardware-free timing/state machine, 25 ms debounce,
  0.5-second inter-tap interval, two-second hold cancellation.
- `button_actions.py`: nonblocking worker with the existing hardware API lock.
- `button_input.py`: read-only transient I2C error recovery, at most four total
  attempts for EIO/EREMOTEIO and three 10 ms waits. Exhaustion/other failures
  remain unknown; the recognizer's fault guard still applies. Returned samples
  are timestamped after reading, not before retries. No registers are written.
- `reframe.py`: imports, button-loop integration, QR defaults, failed-read handling.
- `dashboard.py` and `settings.example.json`: matching QR defaults/help only.
- `tests/test_button_*.py` and `tests/test_qr_settings.py`: regression coverage.

Startup-held input and busy periods require a stable release before recognizing
another gesture. Failed I2C reads additionally require 0.5 seconds of continuous
release, so a trailing tap in an interrupted triple cannot become a photo.
This prevents a spurious capture; it does not repair unreliable communication or
reconstruct taps that the hardware did not report. Worker teardown waits for that worker only;
upstream background photo-save/display threads are separate. This feature is not
a replacement for complete graceful shutdown. Stop services only when idle.

## Tests

Hardware-free recognizer/dispatcher tests:

```sh
python3 -B -m unittest discover -s tests -p 'test_button_*.py' -v
```

Full tests require the existing development environment including Pillow,
FastAPI, and HTTPX:

```sh
.venv/bin/python -B -m unittest discover -s tests -q
```

The initial release passed 47 tests. Four additional fault-recovery regressions
cover interrupted triple taps, repeated faults, busy resets, and the exact quiet
deadline. Input-adapter and synthetic-timing regressions cover bounded retries,
debounce, and the unchanged 500 ms limit. A synthetic replay with 700 ms gaps intentionally remains
separate singles in a 500 ms window, even after read recovery; an alternate
900 ms test window recognizes QR. That alternate value is not a live default.
Source presence and automated tests are not proof of installation or a physical
hardware pass. Brief retries can still miss edges shorter than the sampling
interval; they do not prove the cause of a transient I2C error.

The retry follow-up is based on the combined photo-library release `9ca3601`.
It changes only input reads/integration, tests, and this guide. Gesture thresholds,
shutdown, startup capture, dashboard features, and saved settings are unchanged.
The 30 ms retry-wait budget excludes time spent in the I2C system call. Successful
recovery and exhausted failures are logged separately for physical acceptance.

## Deployment and rollback

1. Inspect installed commit, tracked/untracked changes, runtime Python, service
   paths/states, and available storage. Do not assume a new checkout matches it.
2. Preserve existing edits. Back up code/Git history and private settings on the
   camera outside the checkout, with a unique recovery directory and manifest.
   Keep photos intact; preserve a separate recoverable copy before broad updates.
3. Stage the reviewed release and verify its commit/tree. Do not run the upstream
   installer or replace the entire checkout as a shortcut.
4. Confirm idle: no capture, background save, or panel refresh. Stop camera and
   dashboard services. Activate reviewed code including all three button modules.
5. For the initial QR installation, narrowly set `system.show_dashboard_qr_on_wifi_connect` false and, if present,
   legacy `show_dashboard_qr_on_first_network` false. Preserve all other settings,
   owner/mode, and a private pre-change copy. The retry-only follow-up must preserve
   all settings bytes instead; no new migration is required. Never commit live settings.
6. Compile with the actual camera interpreter before restarting. Restarting
   retains the upstream startup-photo behavior; account for that refresh.
7. Verify services and photo preservation. Physically test single/double/triple
   presses, QR without an extra photo, and unchanged long-press shutdown.
   Leave at least 180 seconds between panel refreshes per the build's handling rule.
8. On failure, first inspect active refresh/save state; never blindly restart or
   roll back while startup is running. Once safely idle, restore the recorded code revision
   and backed-up settings, then restore prior service states. Do not delete photos,
   perform an unrelated update, or rewrite shared Git history.

For this retry-only release, the pre-update recovery revision is `9ca3601`, which
retains all existing Trash journal support. Do not revert the whole camera to an
earlier pre-Trash release; see [photo-library recovery](photo-trash.md).

For planned feature removal, revert its feature commit and explicitly choose
whether to restore the prior automatic-QR setting. A Git revert cannot restore
private runtime settings automatically.

## Future updates

See [customization workflow](customization-workflow.md). The stock updater does
not merge local customizations: tracked edits block it, and custom history ahead
of stock upstream also prevents a fast-forward install. Do not discard changes
to unlock that button. Review/merge/test upstream releases on the development
machine, then deploy the combined release.

Its built-in backup covers settings only, not a complete installation. A full
update can change dependencies and vendor service/helper files; code rollback
alone may not reverse those changes.

## Sources

- [Upstream reFrame](https://github.com/kaloyaan/reframe)
- [PiSugar 3 I2C datasheet](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-i2c)
- [Upstream updater](https://github.com/kaloyaan/reframe/blob/main/dashboard.py)
- [Update helper](https://github.com/kaloyaan/reframe/blob/main/scripts/reframe-apply-update)

Retain upstream attribution and the repository's Apache-2.0 license.
