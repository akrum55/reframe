# Select photos → Move to Trash

Austin's optional dashboard feature, developed on `feature/photo-trash` from the
verified `austin-camera` release `7122fde`, then accepted and deployed as `827754f`
on September 6, 2026. No upstream update or camera-control behavior is changed.

Deployment verification passed: 80 tests on the camera's Python 3.13.5, seven
JavaScript tests locally, private backup verification, and a live dashboard API
round-trip with two synthetic photo pairs restored byte-for-byte. No personal
photo was used for the mutation test. Physical controls were not retested by
this deployment; their code and saved settings were preserved.

## Use

1. Click **select photos** in the gallery, then tap each photo you want to remove.
   Selected cards show a checkmark. **Select this page** selects that page, not
   the whole library. Selections remain selected across pages, up to 100 at once.
2. Choose **move to trash**, then confirm the number of photos.
3. Use **undo last move** immediately, or open **trash** later, select photos,
   and choose **restore selected**. Trash survives browser reloads and camera
   restarts. Undo is a convenience for the last batch in the current browser tab.
4. **Cancel** leaves selection mode without changing any files.

This moves original and processed copies together. It does not refresh the
physical ePaper screen or take a photo. Missing processed copies are allowed.
Files are never automatically purged; Trash continues to use microSD space.
There is intentionally no permanent-delete button inside Trash in this version.

The pre-existing Settings **permanently delete all active photos** action still
bypasses Trash and cannot be undone. Its wording and confirmations make that
distinction explicit. Do not use it when you want recoverable removal.

## Safety and recovery

- Only the hardware service moves files. Capture, display, and reprocess share
  its operation lock. Background saves reserve a counter before starting; Trash
  and restore return a busy response until all saves and display activity finish.
- `.photo-trash/` is private runtime data alongside `photos/` and
  `dithered_photos/`. Never add it to Git or a source-only deployment archive.
  Include all three directories in private photo/recovery backups while services
  are stopped. The dashboard updater lists Trash as preserved user data, but
  its automatic backup copies **settings only**, not photos or Trash. Make the
  complete private photo backup separately before any update or rollback.
- Each entry has a durable journal containing the photo ID, time, expected
  filenames, sizes, and SHA256 hashes. Intent is recorded before moving files;
  interrupted moves are resumed at startup. SHA256/size checks reject replaced
  files. Files are linked/unlinked on the same filesystem without overwriting a
  distinct destination; each directory change is synced. See the
  [Python filesystem primitives](https://docs.python.org/3/library/os.html#os.link).
- A move failure attempts to restore that pair. A failed compensation leaves the
  journal and files intact for recovery. A batch can partially succeed: the UI
  reports successful and failed counts separately; Undo covers successful moves.
- Restoring never overwrites an existing different file or a different original
  sharing its photo ID. Leave conflicting files intact and investigate; do not
  delete files merely to make a restore succeed.
- Numeric IDs in Trash/history remain reserved when the camera restarts, so
  removing the newest photo does not let a subsequent capture reuse its name.
- Normal gallery/ZIP exports exclude Trash. Dashboard mutations, ZIP creation,
  and permanent deletion reserve mutually exclusive jobs before their first
  network await. Missing/unreadable export files now fail the ZIP instead of
  silently producing an incomplete successful download.
- A timeout is not proof nothing moved: check Trash before retrying. Hardware
  may finish an already-started request after the connection drops. Retries of a
  completed move/restore are safe. A copied backup of an *interrupted* hard-link
  operation must preserve hardlinks (normal `tar` does); otherwise recovery
  refuses the duplicate distinct files for manual review rather than overwriting.
- No authentication/cloud system is added. Keep the dashboard on trusted local
  networks; do not expose it directly to the internet. New write routes require
  a same-site custom request header and do not enable cross-origin access.

If Trash says an operation was interrupted, try **restore selected** once the
camera is idle. If it still fails, preserve the active folders and the entire
Trash folder, stop writes, and inspect a private backup. Corrupt journals fail
closed rather than silently forgetting reserved IDs or hiding lost files.

## Local checks and preview

```sh
.venv/bin/python -m unittest discover -s tests -q
node --test tests/photo-library.test.cjs
node --check static/photo-library.js
.venv/bin/python scripts/preview_photo_library.py --port 8765
```

The preview binds only to loopback and uses 16 disposable, synthetic sample
images in a temporary directory. Camera, settings-write, and system-update
operations are disabled. No real photo library is read or uploaded. Open the
printed/local preview address and try selection, confirmation, Undo, Trash,
restore, and page changes. Stop the server with Ctrl-C when finished.

Unit/API tests cover byte preservation, originals with missing/legacy processed
copies, duplicates and stale selections, collision refusal, path/symlink and
journal validation, pending saves, interrupted links/unlinks/commits, copied
private backups, ID reservation across restart, and job exclusion. JavaScript
tests cover selection, cross-page selection, the 100-photo limit, partial
responses, Undo targeting, busy errors, connection loss, and double submits.

## Deployment and future upstream updates

Keep this feature separate until approved, then merge the reviewed commit into
`austin-camera`. Deploy both Python modules, dashboard assets, and narrow core
hooks together; do not deploy only the visible buttons. No new camera dependency
or database is required. Verify the camera interpreter and all tests before
restarting services, with the camera idle and the display rest interval observed.
Back up settings, source/revision, all three private photo directories, and hashes.
Do not use the broad upstream installer merely to deploy this feature.

First device acceptance should use a deliberately disposable photo, confirming
the original/processed pair returns byte-for-byte after restore. Do not use a
personal photo as an unapproved deletion test. Verify existing single capture,
triple QR, manual-only QR setting, and normal gallery/download behavior afterward.

For upstream merges, review `FileManager`, background capture saves, hardware API
registration, dashboard gallery/loading/job handlers, assets, and user-data
backup paths. Run both suites again. Isolation makes conflicts visible but does
not guarantee every future upstream change merges automatically.

To remove the feature, restore wanted photos first, keep a private Trash backup,
then revert the feature's commit on a review branch and deploy a reviewed
release. Reverting software alone does **not** restore trashed photos. Retain
`.photo-trash/` until recovery and the reserved-ID implications are resolved;
stock ID allocation does not know about it.
