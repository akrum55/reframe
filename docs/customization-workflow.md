# Custom release history and maintenance

Original project: [kaloyaan/reframe](https://github.com/kaloyaan/reframe).
Original authorship/history and Apache-2.0 license are preserved.

## Branches and changes

| Branch | Purpose |
| --- | --- |
| `main` | Untouched stock reFrame; tracks `upstream/main` on the Mac |
| `feature/triple-press-qr` | Isolated QR implementation and regression tests |
| `feature/photo-trash` | Optional multi-select/Trash/restore feature; local preview until accepted |
| `austin-camera` | Reviewed combined release deployed to the camera |

Initial stock base: `5b88b443a9225b7954b57bbb784854c081c6991b`.

- `514d69c`: retain the executable installer permission already present on the camera; no installer content changes.
- `8b82d05`: triple-short-press QR feature, QR defaults/help, and tests.
- Release documentation is a separate commit; it does not change camera behavior.

Read-only comparisons:

```sh
git log --oneline 5b88b443a9225b7954b57bbb784854c081c6991b..austin-camera
git diff --stat 5b88b443a9225b7954b57bbb784854c081c6991b..austin-camera
git show 8b82d05
```

## Remote layout

Public custom fork: [akrum55/reframe](https://github.com/akrum55/reframe), with
`austin-camera` as its default branch. On the Mac, `upstream` points to the
original project with pushing disabled; `origin` points to this personal fork.
Keep credentials in the normal GitHub authentication mechanism, never in remote
URLs, commits, config examples, or notes. Git author email uses GitHub noreply.

The camera does not need GitHub credentials because this fork is public. Initial
deployment receives a reviewed Git bundle over authenticated SSH. Verify/import
the release, then activate it while camera/dashboard services are stopped and
the hardware is idle.

After bootstrap, configure its `origin` to the personal fork and track
`origin/austin-camera`; keep the original repository as `upstream`. Only configure
this after verifying the published ref and deployed revision. The stock dashboard
updater can then fast-forward to our reviewed custom releases rather than replace
them with stock code. Until that tracking is configured, it may report divergence
from stock upstream. Never discard changes to unlock installation.

Keep pushing rights/credentials on the Mac only. Prefer guided SSH deployment
with backups and acceptance tests even when the stock update button is available:
it runs the dependency/service helper and is broader than a source-only patch.
There are no unattended merges, installations, or update monitors.

## Bringing in upstream updates

1. Start with a clean Mac checkout and keep an identified, recoverable deployed release.
2. Fetch `upstream`, review changes since the recorded base, and fast-forward local
   `main` only if it remains stock. Never add personal commits to `main`.
3. Create a review branch from `austin-camera` and merge the reviewed upstream
   version. Resolve any conflicts; preserve original history rather than rebasing
   or force-pushing an already deployed release.
4. Run the complete test suite and inspect dependencies, services, configuration
   migrations, and hardware/API changes. New upstream code may require more tests.
5. After approval, advance `austin-camera` to the reviewed result. Back up the
   camera, transfer the release, compile with its interpreter, deploy while idle,
   and perform physical acceptance. Record the release commit and recovery path.

The stock updater refreshes dependencies/services as well as code; a source-only
rollback cannot reliably undo an arbitrary full update. Do not assume its built-in
settings backup covers photos, custom code, or the operating system.

## Removing this feature

Create a new review branch from the release and revert feature commit `8b82d05`.
Review any conflicts with later changes, update the custom README notice, run
tests, then deploy as a new release. Do not reset or force-push shared history.
Reverting source restores stock defaults but does not rewrite private runtime
settings: separately choose whether to restore automatic QR on connection.

Emergency rollback uses the saved pre-deployment revision/settings and recorded
service states; leave photos intact. The deployment record, not this public-safe
source document, identifies device-specific backup locations and acceptance.

## Privacy and scope

Only software, tests, and technical documentation belong here. Live settings,
credentials, personal photos, recovery archives, and project/vault notes do not.
The deployed customization is triple-short-press QR with deferred single capture.
See [QR details](triple-press-qr.md). This feature branch additionally contains
[multi-select photo Trash and restore](photo-trash.md); it must be reviewed and
deployed before it appears on the camera. Preserve `.photo-trash/` with private
photo backups, never in public source or Git, and restore wanted photos before
removing the feature.
