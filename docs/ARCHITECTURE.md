# Architecture

## Domain and master-data boundary

`ScoresheetDocument` remains independent of model and source-file formats. It stores the game header, two teams, semantic fouls/timeouts/team fouls, cumulative score events, separately stated period/final scores, officials, source asset, validation state, and revision metadata.

`master_data.py` is the source adapter. It reads schedule JSONL and the two roster workbooks into derived SQLite tables. Player names are normalized only by Unicode NFKC, trimming, and collapsing whitespace. A normalized duplicate within one team blocks import. Registration jersey-number columns are never read into the domain model or prompt.

Source-specific team-label mappings exist only while joining schedule teams to roster teams. They are not player aliases and never enter `GamePriorSnapshot` or a model request. At document creation, the system freezes:

- game ID, competition, division, local date/time, and venue;
- A/B team IDs and display names;
- each side's canonical player-name list;
- a master-data source hash and locked document paths.

This snapshot is immutable even when the derived master tables are refreshed later.

## Recognition contract

Recognition is an opt-in operation on a persisted document that has both an image and a game prior. `recognition.py` builds a dynamic Pydantic model for that exact game. A team's player `name` is `Literal[that side's names] | None`; all other output fields are concise semantic values.

The provider receives:

1. one fixed system prompt explaining only non-obvious FIBA marks and uncertainty behavior;
2. a short user prompt containing A/B team names;
3. the dynamic JSON Schema, which carries the two name enumerations and one-sentence field descriptions;
4. one EXIF-normalized full-sheet image, resampled in memory toward 6,291,456 pixels (at most 2x per axis) and encoded as high-quality JPEG 4:4:4.

It does not receive registration numbers, internal IDs, aliases, schedule scores, schedule staff, or repeated header values. The Qwen request uses `qwen3.8-max`, `xhigh` thinking, `vl_high_resolution_images=true`, strict JSON Schema output, a fixed seed, no `thinking_budget`/`max_tokens`, and zero automatic retries. It streams the provider response to avoid a long-thinking HTTP timeout; only safe task phases and final usage are exposed to the browser, while raw reasoning text is discarded. The response is validated again locally. `temperature` is intentionally omitted because Qwen3.8 Max thinking mode applies its own minimum/default sampling value.

`RecognitionPayload` contains only team rosters/marks, timeouts, team fouls, coaches and their fouls, each team's sparse cumulative-score sequence, stated period/final results, an unassigned `table_personnel` name list, role-bound referees/signature presence, and one `recognition_notes` string. Every score event includes `cumulative_score`, `scorer_jersey`, and a required `points` value restricted to 1, 2, or 3. The model never maps table personnel onto scorer, assistant-scorer, timer, or shot-clock roles. Score marks and three-point circles are derived from `points`; period ink roles and end-of-game lines are generated deterministically while mapping the payload into `ScoresheetDocument`.

The cache key covers processed image bytes, immutable prior, model, system/user prompts, dynamic Schema, prompt version, and image-preprocessing version. A cache hit creates an auditable recognition run with zero new-call usage and does not invoke the provider.

## Application and merge semantics

Each run records the document's base server revision.

- If the run succeeds while the document is still at that revision and remains recognition-empty, the result is auto-applied as one `recognition` revision.
- If the user has edited the draft, the base revision changed, or this is a rerun, no fields are applied automatically.
- The diff endpoint compares eight semantic regions: each side's roster/fouls, metadata, and running score, plus summary and the combined unassigned-table-personnel/referee region.
- Applying selected regions creates one `recognition_merge` revision. Unselected regions keep their current human-edited values.

The browser tracks server revision separately from undo snapshots. Undo/redo restores semantic content but rebases it onto the current server revision before saving, preventing a restored old snapshot from creating a false 409 conflict.

Recognition metadata carries the run ID, one note, and exceptional JSON paths. Those paths produce editor-only highlights. Neither highlights nor provider metadata are rendered into the black SVG/PDF scene.

## Coordinate and rendering contract

`shared/template_definition.json` is the compact geometry source. It expands into stable cells including player rows, five formal player fouls, three formal cells for each listed coach, post-foul positions, cumulative-score cells, written summary fields, and officials.

Coordinates use PDF points and a top-left origin. SVG consumes them directly. ReportLab converts only the Y axis (`pdf_y = page_height - top_left_y`). Both renderers consume the same semantic data and scene primitives.

The template is A4 at `595.32 × 842.04 pt`. The UI has no freehand input. A single click selects a logical block; a double click selects a measured cell and focuses its semantic editor. Zoom, panning, photo opacity, validation outlines, and recognition outlines do not alter PDF coordinates.

## Rules, validation, and confirmation

Documents select a `rules_profile`. `fiba_2024` remains active. `shared/rule_profiles.json` also contains a disabled FIBA 2026 catalogue so future foul tokens, Schema enums, rendering, and checks can change without changing recognition endpoints or the core document flow. There is no automatic date-based switch.

Validation is deterministic and side-effect free. Any `error` blocks confirmation; all warnings require explicit acknowledgement. Confirmation creates an immutable revision.

## Local security boundary

FastAPI binds to `127.0.0.1`. Uploaded photos, normalized images, SQLite data, cached model results, and exports stay in ignored local directories. `QWEN_API_KEY` is read only by the backend at the instant a live provider request is executed. It is never sent to the frontend or stored in a document, database row, or prompt log.

Public tests inject `MockRecognitionProvider` and a tracked synthetic master-data fixture. The only live test is skipped unless `RUN_QWEN_LIVE=1` is explicitly set; it makes one request and has no retry path.
