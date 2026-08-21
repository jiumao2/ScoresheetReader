# Architecture

## Domain and master-data boundary

`ScoresheetDocument` remains independent of model and source-file formats. It stores the game header, two teams, semantic fouls/timeouts/team fouls, cumulative score events, separately stated period/final scores, officials, source asset, validation state, and an internal optimistic-concurrency counter. The counter is not a user-visible version and no full historical document is retained.

`master_data.py` is the source adapter. It reads schedule JSONL and the two roster workbooks into derived SQLite tables. Player names are normalized only by Unicode NFKC, trimming, and collapsing whitespace. A normalized duplicate within one team blocks import. Registration jersey-number columns are never read into the domain model or prompt.

Source-specific team-label mappings exist only while joining schedule teams to roster teams. They are not player aliases and never enter `GamePriorSnapshot` or a model request. At document creation, the system freezes:

- game ID, competition, division, local date/time, and venue;
- A/B team IDs and display names;
- each side's canonical player-name list;
- a master-data source hash and locked document paths.

This snapshot is immutable even when the derived master tables are refreshed later.

## Recognition contract

Recognition is created automatically when a game-bound photo upload is persisted. `recognition.py` builds a dynamic Pydantic model for that exact frozen game prior. A team's player `name` is `Literal[that side's names] | None`; all other output fields are concise semantic values. Direct uploads without a game prior are not exposed.

The provider receives:

1. one fixed system prompt explaining only non-obvious FIBA marks and uncertainty behavior;
2. a short user prompt containing A/B team names;
3. the dynamic JSON Schema, which carries the two name enumerations and one-sentence field descriptions;
4. one EXIF-normalized full-sheet image, resampled in memory toward 6,291,456 pixels (at most 2x per axis) and encoded as high-quality JPEG 4:4:4.

It does not receive registration numbers, internal IDs, aliases, schedule scores, schedule staff, or repeated header values. The Qwen request uses `qwen3.8-max`, `xhigh` thinking, `vl_high_resolution_images=true`, strict JSON Schema output, a fixed seed, and no `thinking_budget`/`max_tokens`. It streams the provider response to avoid a long-thinking HTTP timeout; only safe task phases and final usage are exposed to the browser, while raw reasoning text is discarded. The response is validated again locally. Model/content failures are never retried automatically; an explicit provider rate-limit rejection may be requeued once after reducing local concurrency to one.

`RecognitionPayload` contains only team rosters/marks, timeouts, team fouls, coaches and their fouls, each team's sparse cumulative-score sequence, stated period/final results, an unassigned `table_personnel` name list, role-bound referees/signature presence, and one `recognition_notes` string. Every score event includes `cumulative_score`, `scorer_jersey`, and a required `points` value restricted to 1, 2, or 3. The model never maps table personnel onto scorer, assistant-scorer, timer, or shot-clock roles. Score marks and three-point circles are derived from `points`; period ink roles and end-of-game lines are generated deterministically while mapping the payload into `ScoresheetDocument`.

The cache key still covers processed image bytes, immutable prior, model, system/user prompts, dynamic Schema, prompt version, and image-preprocessing version for compatibility and audit. Upload and reupload runs deliberately bypass cached results: even byte-identical reuploads create a fresh paid-provider request.

## Application and merge semantics

Each run records the document's base server counter, immutable source-image version/hash, trigger, retry count, and superseding run.

- If the run succeeds while the document is still at that counter and remains recognition-empty, the result is auto-applied to the current document. Automatic recognition is not shown as a human modification.
- A successful source version cannot be manually rerun. A failed or restart-interrupted run may be retried from the editor.
- Reupload uses the same document ID, advances the internal counter, resets scoresheet content, alignment, validation, and recognition metadata, records only a compact reupload action, then queues a non-cached run. Pending older work is superseded; already-running older work may finish but can never apply to the replacement.
- Existing diff/apply endpoints remain readable for legacy runs, but the normal upload workflow no longer creates selective reruns.

SQLite is the durable queue. FastAPI lifespan starts a configurable worker pool (`SCORESHEET_RECOGNITION_CONCURRENCY`, default `2`), workers atomically claim pending runs in FIFO order, and only one run per document executes at a time. Browser navigation only changes the current SSE subscription and never cancels server work. Pending work resumes after restart; in-flight work is marked `interrupted` rather than automatically repeated and potentially billed twice.

The browser tracks the server counter separately from in-memory undo snapshots. Undo/redo restores semantic content only for the current browser session and rebases it onto the current server counter before saving, preventing a restored local snapshot from creating a false 409 conflict.

## Current-document and human-log persistence

SQLite stores exactly one current JSON payload per document. `revision/base_revision` remains an internal compare-and-swap mechanism for autosave and recognition safety, but the UI does not expose it as a document version.

`document_change_logs` stores only human-facing actions: human edits, undo, redo, selectively applying a recognition diff, reupload, and confirmation. Ordinary edits are diffed into stable field paths and before/after values. Reupload and confirmation store action summaries only; automatic recognition, initial creation, pan/zoom, and other view state are excluded. `GET /api/v1/documents/{id}/changes` is paginated and never returns a full document snapshot or rollback capability.

On the first startup after upgrade, legacy adjacent `document_revisions` snapshots are converted transactionally into compact readable changes, the latest payload in `documents` is left untouched, and the legacy snapshot table is dropped. A migration marker makes this idempotent.

Recognition metadata carries the run ID, one note, and exceptional JSON paths. Those paths produce editor-only highlights. Neither highlights nor provider metadata are rendered into the black SVG/PDF scene.

## Coordinate and rendering contract

`shared/template_definition.json` is the compact geometry source. It expands into stable cells including player rows, five formal player fouls, three formal cells for each listed coach, post-foul positions, cumulative-score cells, written summary fields, and officials.

Coordinates use PDF points and a top-left origin. SVG consumes them directly. ReportLab converts only the Y axis (`pdf_y = page_height - top_left_y`). Both renderers consume the same semantic data and scene primitives.

The template is A4 at `595.32 × 842.04 pt`. The UI has no freehand input. A single click selects a logical block; a double click selects a measured cell and focuses its semantic editor. Zoom, panning, photo opacity, validation outlines, and recognition outlines do not alter PDF coordinates.

## Rules, validation, and confirmation

Documents select a `rules_profile`. `fiba_2024` remains active. `shared/rule_profiles.json` also contains a disabled FIBA 2026 catalogue so future foul tokens, Schema enums, rendering, and checks can change without changing recognition endpoints or the core document flow. There is no automatic date-based switch.

Validation is deterministic and side-effect free. Any `error` blocks confirmation; all warnings require explicit acknowledgement. Confirmation updates the current document status and appends a compact confirmation action; it does not retain another full document copy.

## Local security boundary

FastAPI binds to `127.0.0.1`. Versioned uploaded photos, normalized images, SQLite task/result data, and exports stay in ignored local directories. `QWEN_API_KEY` is read only by the backend at the instant a queued live request executes. It is never sent to the frontend or stored in a document, database row, or prompt log. Uploading a game photo is the user action that authorizes immediate transfer to the configured provider.

Public tests inject `MockRecognitionProvider` and tracked synthetic master data that are available only to test/development setup. The production UI and production API expose no synthetic scoresheet fixture. The only live test is skipped unless `RUN_QWEN_LIVE=1` is explicitly set; it makes one request and has no retry path.
