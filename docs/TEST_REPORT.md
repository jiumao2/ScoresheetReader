# Test Report

Date: 2026-08-20

## Verified public test state

- Prompt and Schema: the current Chinese prompt and sparse per-team `running_score` contract are covered by backend tests. Every score event returns `cumulative_score`, `scorer_jersey`, and `points` restricted to 1, 2, or 3.
- Backend: 77 tests passed and the explicitly paid live-Qwen test was skipped. Ruff lint and format checks completed with no findings.
- Frontend: 8 Vitest files and 61 tests passed. TypeScript checking and the Vite production build succeeded.
- Browser: 7 public Playwright workflows passed; the opt-in private read-only workflow was skipped.
- Cost: all checks used the deterministic Mock provider. No Qwen request was made and Qwen token usage was zero.
- Privacy: private photos, schedules, rosters, local SQLite databases, templates, and generated artifacts remained outside the public commit set.

## Covered behavior

- schedule/roster preprocessing, stable master-data IDs, duplicate-name rejection, and immutable game-prior snapshots;
- upload validation, document revisions, optimistic concurrency, validation, confirmation, SVG rendering, and PDF export;
- dynamic canonical-name Schema, structured Qwen request parameters, response validation, cache reuse, sparse running-score compatibility, recognition diff, auto-apply, and selective merge;
- semantic editing, undo/redo, autosave and refresh recovery, game selection, recognition status, score-event insertion/editing/deletion, field navigation, pane resizing, photo pan/zoom/reload, and printable export;
- rule-profile-driven foul marks and deterministic score/final-result validation.

## Expected non-blocking warnings

- Starlette reports its current `httpx` compatibility deprecation warning.
- pypdf reports the upstream `PageObject.replace_contents()` deprecation warning.
- Vite reports that the PDF.js worker and main bundle exceed its default chunk-size warning threshold; the production build still succeeds.

## Not covered by default checks

- live Qwen accuracy, token usage, and provider availability;
- private-photo visual comparison;
- future FIBA 2026 profile activation;
- WeChat Mini Program, accounts, cloud storage, and multi-user collaboration.

The paid integration test runs only when `RUN_QWEN_LIVE=1` is explicitly set. The private browser check runs only when `RUN_PRIVATE_LIVE_UI=1` is explicitly set. Neither belongs to CI or the default test command.
