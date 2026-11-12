# Application Sources

Edit these source files instead of the generated `static/js/app.js`. Run
`python scripts/build_assets.py` after changing JavaScript, CSS, or local assets
referenced by `static/index.html`. CI and deployment checks can run
`python scripts/build_assets.py --check` to reject stale generated files.

The build uses Python's standard library and preserves one classic script with
the existing global functions and HTML callback contract. It does not wrap the
sources in modules or change declaration hoisting. `APP_PARTS` in the build
script defines the order:

- `core.js`: shared state, navigation utilities, gamification, and PK controls.
- `account.js`: parent settings, account settings, avatars, and invitations.
- `leaderboard.js`: leaderboard rendering and request state.
- `review-input.js`: shared dialogs, answer inputs, audio, and speech playback.
- `api.js`: API requests and shared wordbank download cache.
- `session.js`: authentication, session lifecycle, page bootstrap, and routing.
- `learning.js`: review queues, exercise submission, progress, and learning map.
- `wordbank.js`: progress lists, discovery, wordbank search, and import workflows.
- `bootstrap.js`: DOM event wiring and application startup.

Account-scoped asynchronous work captures `captureAccountContext()` before its
first request and checks `isAccountContextCurrent()` after asynchronous steps.
Session activation and cleanup belong to `session.js`; account teardown clears
review state, list observers, import tasks, private UI, textbook state, and chat.
Review work also captures its independent review generation so leaving or
restarting a queue invalidates older responses.

The build hashes the bytes served for local HTML asset references, copies them
to `static/assets/` with content hashes in their filenames, and writes their
URLs and full SHA-256 digests to `static/asset-manifest.json`. Existing copies
are retained so older browser sessions can still request immutable URLs. Deploy
application files and run the build before switching traffic to a new release.
HTML itself must remain revalidated. Retain the previous release's asset copies
when atomically switching release directories or container versions.
