# ScoresheetReader

**English** | [简体中文](README.zh-CN.md)

ScoresheetReader is a local-first basketball scoresheet digitization tool. A user selects a scheduled game, uploads one full-sheet photo, optionally asks a VLM for a structured draft, reviews that draft directly in the visual scoresheet editor, runs deterministic checks, and exports a PDF.

Recognition does not use OCR. Live requests use `qwen3.8-max` through Alibaba Cloud's OpenAI-compatible endpoint. Public tests and CI use a deterministic zero-token mock and never read `QWEN_API_KEY`.

## Current capabilities

- Import schedule JSONL plus men's and women's roster workbooks into derived SQLite master data. Player registration numbers are ignored; only an internal ID, team, and one canonical name are retained.
- Show `not uploaded`, `uploaded`, `recognized`, and `submitted` states in the game picker, and open an existing scoresheet directly from its game row. New drafts freeze a `GamePriorSnapshot`; competition, date, time, venue, and A/B team names are prefilled and locked while game number remains blank and editable.
- Recognize the entire image in one request after EXIF normalization, roughly 6.3 MP whole-image resampling, high-quality JPEG 4:4:4 encoding, and `vl_high_resolution_images=true`. There is no OCR, auto-crop, or perspective correction.
- Build a dynamic Pydantic schema whose player-name fields accept only that side's canonical names or `null`. The response has no confidence values, alternatives, aliases, internal IDs, or chain of thought.
- Auto-apply the first result only to an unchanged empty draft. A rerun produces region-level differences and requires an explicit selective merge, preserving unselected human edits.
- Stream safe recognition phases and show only notes, exceptional/null locations, deterministic conflicts, and exact token usage from the final API chunk. Raw reasoning text is discarded; editor highlights never enter SVG/PDF output.
- Use a draggable three-pane workspace with photo and reconstructed-sheet pan/zoom, persistent splitters, reload, overlay, semantic controls, undo/redo, 750 ms autosave, explicit draft save, revision history, validation, and final submission. A failed or conflicting save stops validation and submission so stale server data cannot be confirmed.
- Render the same semantic document through browser SVG and a ReportLab overlay merged with the source template by pypdf.

## Flow and architecture

```text
schedule/rosters -> SQLite master data -> immutable game prior
                                               |
photo -> Qwen structured response -> ScoresheetDocument draft
                                               |
                                  React + PDF.js + SVG editor
                                               |
                         deterministic validation -> review -> PDF
```

Identical image, prior, model, prompt, schema, and preprocessing versions hit the local cache and do not make a second model request. See the [architecture documentation](docs/ARCHITECTURE.md) and [FIBA notation audit](docs/fiba-notation-audit.md).

## Install

Requirements are Conda, Node.js/npm, a local untracked `scoresheet_template.pdf`, and a TrueType Chinese font for PDF export.

```powershell
conda create -n scoresheet-reader python=3.11
conda activate scoresheet-reader
python -m pip install -e ".\backend[dev]"
npm install
```

Place the template at the repository root or set `SCORESHEET_TEMPLATE_PATH`. The next section documents master-data preparation, preprocessing, editor reads, and persistence. Other settings are listed in [.env.example](.env.example). The application does not load `.env` files automatically; set variables in the same terminal that starts the backend.

## Data preparation, preprocessing, and persistence

### 1. Prepare private master data

Keep private inputs in a directory outside the repository. It needs these three kinds of files:

```text
C:\private\scoresheet-master-data\
├── Schedule_2026北大杯.json
├── 男篮.xlsx
└── 女篮.xlsx
```

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

This synchronization does not delete uploaded images, document drafts, revision history, or recognition results. Restart the backend after changing an input. `GET /api/v1/health` reports `master_data: ready` on success, and the games API returns import errors explicitly.

Creating a document freezes the current game ID, metadata, A/B canonical team names, and both unique-name lists into a `GamePriorSnapshot`. Later roster imports therefore do not silently mutate existing documents; create a new document from the game picker to use new master data.

### 3. Where the editor reads data

The browser never opens Excel, JSONL, or SQLite directly:

- the game picker reads preprocessed games from `GET /api/v1/games` and a prior from `GET /api/v1/games/{id}`;
- upload calls `POST /api/v1/games/{id}/documents`, which creates a draft with the frozen prior;
- the editor reads and autosaves through `GET/PATCH /api/v1/documents/{id}`, and loads the photo from the document's `source` URL;
- browser `localStorage` keeps only the last-opened document ID, pane layout, and similar UI state, plus an unsaved synthetic-preview draft. The backend SQLite database is authoritative for real games.

### 4. Where results are saved

The default data directory is `data/` inside the repository. For long-term use, point it outside the repository:

```powershell
$env:SCORESHEET_DATA_DIR = "D:\ScoresheetReaderData"
```

| Content | Default location or behavior |
| --- | --- |
| Derived master data, current document JSON, every revision, raw recognition results, notes, cache keys, and token usage | `data/scoresheet_reader.sqlite3` |
| Uploaded originals, EXIF-normalized copies, and optional aligned images | `data/uploads/`, prefixed by document UUID |
| SVG preview | Generated on demand by `/api/v1/documents/{id}/render.svg`; not persisted automatically |
| PDF export | Generated on demand by `/api/v1/documents/{id}/render.pdf`; saved to the browser-selected download location |
| Private schedule and rosters | Remain in `SCORESHEET_MASTER_DATA_DIR` and are never modified |

Git ignores `data/`, private inputs, the local template, and generated outputs. With the backend stopped, back up the complete `SCORESHEET_DATA_DIR`: copying only SQLite loses the source photos, while copying only `uploads/` loses the edited and submitted structured records.

## Run locally

```powershell
scoresheet-reader
```

In a second terminal:

```powershell
npm run dev
```

Open `http://127.0.0.1:5173`. Both services bind to `127.0.0.1` only.

Only a user-triggered live recognition needs a key, read exclusively by the backend:

```powershell
$env:QWEN_API_KEY = "your-key"
$env:QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:QWEN_MODEL = "qwen3.8-max"
$env:QWEN_REASONING_EFFORT = "xhigh"
```

The exact system prompt, user-prompt builder, and dynamic schema live in [recognition.py](backend/scoresheet_reader/recognition.py).

## Tests

```powershell
python -m pytest backend\tests
python -m ruff check backend scripts\private_photo_check.py
npm test
npm run build
npm run test:e2e
```

Default tests are mock-only: zero Qwen calls, tokens, and cost. The single-request private live check is additionally gated by `RUN_QWEN_LIVE=1` and is never part of CI. See the [current test report](docs/TEST_REPORT.md).

## Privacy and repository policy

- Private photos/master data, local databases, templates, generated PDFs, screenshots, and environment files are ignored.
- Keys are not stored in the frontend, database, or logs.
- Registration jersey numbers, schedule scores/staff, internal IDs, and source join aliases never enter the prompt.
- The table-official area is recognized as a deduplicated list of people only. The model never assigns scorer, assistant scorer, timer, or shot-clock roles; optional paper-role fields remain manually editable. Table personnel, referees, and signatures may all be empty without a required-field warning.
- Public CI uses [synthetic master data](shared/demo_master_data.json) and the mock provider only.

FIBA 2024 remains the active rules profile. A separate, disabled FIBA 2026 catalogue keeps the interface extensible without date-based automatic switching. WeChat Mini Program support, accounts, cloud storage, collaboration, and PKUBA integration remain out of scope.

## License

GNU General Public License v3.0 or later. See [LICENSE](LICENSE).
