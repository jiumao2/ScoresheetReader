# Test Report

Date: 2026-08-23

## Verified public test state

- Prompt and Schema: the current Chinese prompt and sparse per-team `running_score` contract are covered by backend tests. Every score event returns `cumulative_score`, `scorer_jersey`, and `points` restricted to 1, 2, or 3.
- Backend: 103 tests passed and 2 explicitly gated private/paid tests were skipped. Total coverage was 87.96%, above the enforced 85% gate. Ruff lint completed with no findings.
- Frontend: 10 Vitest files and 80 tests passed. Coverage was 69.44% statements, 62.43% branches, 68.25% functions, and 74.53% lines; all four enforced gates passed. TypeScript checking and the Vite production build succeeded.
- Browser: 6 public Playwright workflows passed; the opt-in private read-only workflow was skipped. The runner used isolated random ports, formal game uploads, a temporary SQLite database, and removed its temporary data after completion.
- Cost: all checks used the deterministic Mock provider. No Qwen request was made and Qwen token usage was zero.
- Privacy: private photos, schedules, rosters, local SQLite databases, and generated artifacts remained outside the public commit set. The standard root template is the only committed PDF.

## Covered behavior

- schedule/roster preprocessing, stable master-data IDs, duplicate-name rejection, and immutable game-prior snapshots;
- upload validation, latest-document persistence, optimistic concurrency, compact human field-change logs, legacy snapshot migration/drop, validation, confirmation, SVG rendering, and PDF export;
- dynamic canonical-name Schema, structured Qwen request parameters, response validation, sparse running-score compatibility, automatic upload recognition, source-version-bound auto-apply, and same-image reupload cache bypass;
- durable FIFO recognition scheduling, configurable parallelism, per-document serialization, one-time rate-limit fallback, restart recovery, superseding in-flight work, and failed-run retry;
- blank-template startup, real-document recovery, legacy synthetic-ID rejection, semantic editing, undo/redo, autosave and refresh recovery, game selection, live recognition status, score-event insertion/editing/deletion, field navigation, pane resizing, equal-height photo/template canvases, photo pan/zoom/reset/reload, and printable export;
- rule-profile-driven foul marks and deterministic score/final-result validation.
- generic PATCH server-owned-field protection, atomic concurrent saves, active-recognition deduplication, save/validate/confirm race handling, cross-document recognition isolation, duplicate-period detection, streamed uploads without the former 25 MB/40 MP project cutoffs, Pillow decompression-bomb protection, native-resolution Qwen payloads, exact 20,000,000-byte Data URI enforcement, subject-specific foul validation, conflict recovery, keyboard editing access, and absence of production synthetic-fixture endpoints.

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
