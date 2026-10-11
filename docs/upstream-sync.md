# Automated upstream update preparation

This public fork checks the official `kaloyaan/reframe` `main` branch once a day
and can also be checked manually from GitHub Actions. When official upstream has
new commits, the workflow:

1. starts from the current `austin-camera` release;
2. merges official upstream into a dedicated `automation/upstream-*` branch;
3. runs the Python, JavaScript, compile, and whitespace checks; and
4. opens or refreshes a pull request into `austin-camera`.

It deliberately does **not** merge that pull request or install it on the camera.
Upstream changes can affect hardware behavior, services, dependencies, and saved
settings, so the pull request remains a review and acceptance gate. Merge
conflicts or failed tests stop the workflow rather than changing the release.

After a reviewed pull request is merged, the camera dashboard sees a normal
fast-forward update from its tracked public `origin/austin-camera` branch. Make a
complete private backup of active photos, Trash contents, settings, and the
deployed revision before installing. The built-in updater preserves ordinary
ignored data but is not a substitute for that separate recovery copy.

The workflow uses GitHub's repository token with job-scoped `contents: write` and
`pull-requests: write` permissions. It force-updates only its own deterministic
`automation/upstream-*` review branch; it never rewrites `austin-camera`.

If the pull-request step reports that GitHub Actions may not create pull requests,
enable **Allow GitHub Actions to create and approve pull requests** in the fork's
Actions settings. Approval rights are not otherwise required because the workflow
does not approve or merge its own pull request.
