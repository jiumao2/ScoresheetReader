# ScoresheetReader

**English** | [简体中文](README.zh-CN.md)

ScoresheetReader is a local-first basketball scoresheet digitization tool. A user selects a scheduled game and uploads one full-sheet photo; the backend immediately queues VLM recognition, then the user reviews the structured draft directly in the visual editor, runs deterministic checks, and exports a PDF.

Recognition does not use OCR. Live requests use `qwen3.8-max` through Alibaba Cloud's OpenAI-compatible endpoint. Public tests and CI use a deterministic zero-token mock and never read `QWEN_API_KEY`.

## Current capabilities

- Import schedule JSONL plus men's and women's roster workbooks into derived SQLite master data. Player registration numbers are ignored; only an internal ID, team, and one canonical name are retained.
- Show `not uploaded`, `recognizing`, `recognized`, `recognition failed`, and `submitted` states in the game picker. Upload starts recognition server-side, and switching games does not cancel queued or running work.
- Restore only the last valid game-bound document. With no restorable document, the product opens on the real blank PDF template with editing/submission disabled; synthetic scoresheets are not exposed by the production UI or API.
- Recognize the entire image in one request after EXIF normalization. Images below 8 MP are enlarged toward 8,000,000 pixels at no more than 2x per axis; larger JPEG/PNG inputs keep their native dimensions and bytes when possible. The complete Base64 Data URI is capped at 20,000,000 bytes by preserving dimensions and selecting the highest fitting JPEG quality only when necessary. Requests set `vl_high_resolution_images=true`; there is no OCR, auto-crop, perspective correction, or client-side downscaling of large images.
- Build a dynamic Pydantic schema whose player-name fields accept only that side's canonical names or `null`. The response has no confidence values, alternatives, aliases, internal IDs, or chain of thought.
- Auto-apply the upload result only to its unchanged empty image revision. Successful images cannot be manually rerun; a technical failure can be retried. Reupload replaces the current draft and always performs a fresh provider call, even for byte-identical files.
- Stream safe recognition phases and show only notes, exceptional/null locations, deterministic conflicts, and exact token usage from the final API chunk. Raw reasoning text is discarded; editor highlights never enter SVG/PDF output.
- Use a draggable three-pane workspace with equal-height photo/template canvases, pan/zoom, persistent splitters, reload, overlay, semantic controls, undo/redo, 750 ms autosave, explicit draft save, a compact human field-change log, validation, and final submission. A failed or conflicting save stops validation and submission so stale server data cannot be confirmed.
- Render the same semantic document through browser SVG and a ReportLab overlay merged with the source template by pypdf.

## Flow and architecture

```text
schedule/rosters -> SQLite master data -> immutable game prior
                                               |
photo -> persistent recognition queue -> Qwen response -> ScoresheetDocument draft
                                               |
                                  React + PDF.js + SVG editor
                                               |
                         deterministic validation -> review -> PDF
```

Upload-triggered runs deliberately bypass the legacy result cache, so every reupload is a new recognition. See the [architecture documentation](docs/ARCHITECTURE.md) and [FIBA notation audit](docs/fiba-notation-audit.md).

## Install

Requirements are Conda, Node.js/npm, and a TrueType Chinese font for PDF export. The repository includes `scoresheet_template.pdf`; set `SCORESHEET_TEMPLATE_PATH` only when substituting another template.

```powershell
conda create -n scoresheet-reader python=3.11
conda activate scoresheet-reader
python -m pip install -e ".\backend[dev]"
npm install
npx playwright install chromium
```

The repository root includes [scoresheet_template.pdf](scoresheet_template.pdf). Set `SCORESHEET_TEMPLATE_PATH` to use another template. The next section documents master-data preparation, preprocessing, editor reads, and persistence. Other settings are listed in [.env.example](.env.example). The application does not load `.env` files automatically; set variables in the same terminal that starts the backend.

## Data preparation, preprocessing, and persistence

### 1. Prepare private master data

Keep private inputs in a directory outside the repository. It needs these three kinds of files:

```text
C:\private\scoresheet-master-data\
├── Schedule_2026北大杯.json
├── 男篮.xlsx
└── 女篮.xlsx
```

The repository also includes a synthetic [minimal master-data example](examples/minimal-data/README.md). Point `SCORESHEET_MASTER_DATA_DIR` at `examples/minimal-data/` to exercise schedule preprocessing without private names. The root [scoresheet_template.pdf](scoresheet_template.pdf) is the bundled scoresheet template.

- `Schedule_*.json` is JSONL despite its extension: every non-empty line is one game object. Files are sorted by name and only the first match is loaded, so keep one active schedule in the directory.
- Each game needs `_id`, `group`, `home_team`, `away_team`, `time.$date`, and `place`. `time.$date` should be an ISO 8601 timestamp with an offset; it is converted to `Asia/Shanghai` local date and time.
- `男篮.xlsx` and `女篮.xlsx` may contain sheets named `男甲`, `男乙`, `女甲`, and `女乙`. Reading starts at row 2: column A is the team name and column B is the player's unique canonical name. Other columns, including registration jersey numbers, are ignored.
- Names are normalized only with Unicode NFKC, surrounding-whitespace removal, and repeated-space collapse. A duplicate normalized player name within one division and team rejects the import.
- Schedule teams must join to roster teams in the same division. An unresolved game remains visible but upload is disabled until the source is fixed and the backend restarted.

Minimal schedule line:

```json
{"_id":"game-001","group":"男甲","home_team":"示例学院甲","away_team":"示例学院乙","time":{"$date":"2026-03-21T10:00:00+08:00"},"place":"示例体育馆"}
```

Set the directory and competition name in the PowerShell session that starts the backend:

```powershell
$env:SCORESHEET_MASTER_DATA_DIR = "C:\private\scoresheet-master-data"
$env:SCORESHEET_COMPETITION_NAME = "2026北大杯"
```

For a public demo or local development without private files, use the synthetic fixture instead:

```powershell
$env:SCORESHEET_MASTER_FIXTURE_PATH = "$PWD\shared\demo_master_data.json"
```

See [demo_master_data.json](shared/demo_master_data.json) for its format. `SCORESHEET_MASTER_FIXTURE_PATH` takes precedence over the private master-data directory.

### 2. When preprocessing runs

There is no manual intermediate-data command. Each `scoresheet-reader` startup:

1. reads and validates the schedule and rosters, then normalizes team and player names;
2. generates stable internal IDs and joins schedule teams to roster teams;
3. hashes the three source files with SHA-256;
4. atomically replaces the derived SQLite game/team/player tables only when that source hash changes.

This synchronization does not delete uploaded images, current document drafts, human change logs, or recognition results. Restart the backend after changing an input. `GET /api/v1/health` reports `master_data: ready` on success, and the games API returns import errors explicitly.

Creating a document freezes the current game ID, metadata, A/B canonical team names, and both unique-name lists into a `GamePriorSnapshot`. Later roster imports therefore do not silently mutate existing documents; create a new document from the game picker to use new master data.

### 3. Where the editor reads data

The browser never opens Excel, JSONL, or SQLite directly:

- the game picker reads preprocessed games from `GET /api/v1/games` and a prior from `GET /api/v1/games/{id}`;
- upload calls `POST /api/v1/games/{id}/documents`, which creates the draft and recognition run together;
- reupload calls `PUT /api/v1/documents/{id}/source`, resets the current document at a new revision, and creates a fresh non-cached run; `GET /api/v1/documents/{id}/recognitions/latest` restores progress after navigation or refresh;
- the editor reads and autosaves through `GET/PATCH /api/v1/documents/{id}`, and loads the photo from the document's `source` URL;
- `GET /api/v1/documents/{id}/changes` returns the paginated human field-change log. It never returns a full historical document and has no rollback endpoint;
- browser `localStorage` keeps only the last-opened real document ID, pane layout, and similar UI preferences. An invalid or legacy synthetic ID is cleared, and SQLite remains authoritative.

### 4. Where results are saved

The default data directory is `data/` inside the repository. For long-term use, point it outside the repository:

```powershell
$env:SCORESHEET_DATA_DIR = "D:\ScoresheetReaderData"
```

| Content | Default location or behavior |
| --- | --- |
| Derived master data, the latest document JSON, compact human field changes, raw recognition results, notes, cache keys, and token usage | `data/scoresheet_reader.sqlite3` |
| Versioned uploaded originals, EXIF-normalized copies, and optional aligned images | `data/uploads/`, prefixed by document UUID and source version |
| SVG preview | Generated on demand by `/api/v1/documents/{id}/render.svg`; not persisted automatically |
| PDF export | Generated on demand by `/api/v1/documents/{id}/render.pdf`; saved to the browser-selected download location |
| Private schedule and rosters | Remain in `SCORESHEET_MASTER_DATA_DIR` and are never modified |

Git ignores `data/`, private inputs, replacement local templates, and generated outputs; the standard root template remains tracked. With the backend stopped, back up the complete `SCORESHEET_DATA_DIR`: copying only SQLite loses the source photos, while copying only `uploads/` loses the edited and submitted structured records.

## Run locally

```powershell
scoresheet-reader
```

In a second terminal:

```powershell
npm run dev
```

Open `http://127.0.0.1:5173`. Both services bind to `127.0.0.1` only.

Set the key before uploading a real game photo. Upload starts recognition automatically, and the key is read exclusively by the backend:

```powershell
$env:QWEN_API_KEY = "your-key"
$env:QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:QWEN_MODEL = "qwen3.8-max"
$env:QWEN_REASONING_EFFORT = "xhigh"
$env:SCORESHEET_RECOGNITION_UPSCALE_TARGET_PIXELS = "8000000"
$env:SCORESHEET_RECOGNITION_CONCURRENCY = "2"
```

Uploads are streamed to local storage without a project-specific byte limit or 40 MP cutoff. Pillow's built-in decompression-bomb protection remains enabled. Qwen preparation preserves native dimensions; WebP images above 4K and payloads whose Base64 Data URI would exceed 20,000,000 bytes are converted to same-size JPEG, using the highest fitting quality. A payload that cannot fit even at JPEG quality 1 fails before any paid request.

The exact system prompt, user-prompt builder, and dynamic schema live in [recognition.py](backend/scoresheet_reader/recognition.py).

## Tests

```powershell
python -m pytest backend\tests --cov=backend\scoresheet_reader --cov-fail-under=85
python -m ruff format --check backend scripts\private_photo_check.py
python -m ruff check backend scripts\private_photo_check.py
npm run test:coverage
npm run build
npm run test:e2e
```

The public browser runner allocates isolated random ports, starts its own mock backend, and uses Playwright's bundled Chromium; it rejects a missing/empty report or a zero-test run. Default tests are mock-only: zero Qwen calls, tokens, and cost. The single-request private live check is additionally gated by `RUN_QWEN_LIVE=1` and is never part of CI. See the [current test report](docs/TEST_REPORT.md).

The private read-only browser audit is intentionally separate from `npm run test:e2e`. Start the already configured private backend and frontend first, then opt in explicitly:

```powershell
$env:RUN_PRIVATE_LIVE_UI = "1"
$env:SCORESHEET_E2E_BASE_URL = "http://127.0.0.1:5173"
npm --workspace frontend run test:e2e:private
```

## Privacy and repository policy

- Private photos/master data, local databases, generated PDFs, screenshots, and environment files are ignored. Only the standard root template PDF is version-controlled.
- Keys are not stored in the frontend, database, or logs. Selecting a game and uploading its photo immediately sends the processed whole image, A/B team names, and canonical-name enums to the configured Qwen endpoint. Do not upload until this transfer is intended.
- Registration jersey numbers, schedule scores/staff, internal IDs, and source join aliases never enter the prompt.
- The table-official area is recognized as a deduplicated list of people only. The model never assigns scorer, assistant scorer, timer, or shot-clock roles; optional paper-role fields remain manually editable. Table personnel, referees, and signatures may all be empty without a required-field warning.
- Public CI uses [synthetic master data](shared/demo_master_data.json) and the mock provider only.

FIBA 2024 remains the active rules profile. A separate, disabled FIBA 2026 catalogue keeps the interface extensible without date-based automatic switching. WeChat Mini Program support, accounts, cloud storage, collaboration, and PKUBA integration remain out of scope.

## License

GNU General Public License v3.0 or later. See [LICENSE](LICENSE).
