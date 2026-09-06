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
- `reframe.py`: imports, button-loop integration, QR defaults, failed-read handling.
- `dashboard.py` and `settings.example.json`: matching QR defaults/help only.
- `tests/test_button_*.py` and `tests/test_qr_settings.py`: regression coverage.

Startup-held input, failed I2C reads, and busy periods require a stable release
before recognizing another gesture. Worker teardown waits for that worker only;
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

47 tests passed during local preparation/review. Source presence and automated
tests are not proof of installation or a physical hardware pass.

## Deployment and rollback

1. Inspect installed commit, tracked/untracked changes, runtime Python, service
   paths/states, and available storage. Do not assume a new checkout matches it.
2. Preserve existing edits. Back up code/Git history and private settings on the
   camera outside the checkout, with a unique recovery directory and manifest.
   Keep photos intact; preserve a separate recoverable copy before broad updates.
3. Stage the reviewed release and verify its commit/tree. Do not run the upstream
   installer or replace the entire checkout as a shortcut.
4. Confirm idle: no capture, background save, or panel refresh. Stop camera and
   dashboard services. Activate reviewed code including both new button modules.
5. Narrowly set `system.show_dashboard_qr_on_wifi_connect` false and, if present,
   legacy `show_dashboard_qr_on_first_network` false. Preserve all other settings,
   owner/mode, and a private pre-change copy. Never commit live settings.
6. Compile with the actual camera interpreter before restarting. Restarting
   retains the upstream startup-photo behavior; account for that refresh.
7. Verify services and photo preservation. Physically test single/double/triple
   presses, QR without an extra photo, and unchanged long-press shutdown.
   Leave at least 180 seconds between panel refreshes per the build's handling rule.
8. On failure, stop services while idle and restore the recorded code revision
   and backed-up settings, then restore prior service states. Do not delete photos,
   perform an unrelated update, or rewrite shared Git history.

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
