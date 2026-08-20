# Contributing

Use the `scoresheet-reader` Conda environment with Python 3.11. Keep changes recognition-independent during Phase 1 and never add real scoresheet photos, personal names, API keys, SQLite files, generated PDFs, or private manual fixtures to Git.

Before submitting a change, run:

```powershell
conda activate scoresheet-reader
python -m ruff format --check backend scripts\private_photo_check.py
python -m ruff check backend scripts\private_photo_check.py
python -m pytest backend\tests
npm test
npm run build
npm run test:e2e
```

Keep `RUN_QWEN_LIVE` and `RUN_PRIVATE_LIVE_UI` unset during the default public checks. Those flags opt into private, non-CI tests.

New rendering behavior should be represented as semantic data, implemented in both SVG/PDF paths through the shared scene contract, and covered by coordinate and visual regression tests.
