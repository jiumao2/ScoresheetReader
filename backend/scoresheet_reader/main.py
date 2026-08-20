from __future__ import annotations

import os

import uvicorn


def run() -> None:
    uvicorn.run(
        "scoresheet_reader.api:app",
        host="127.0.0.1",
        port=int(os.getenv("SCORESHEET_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    run()
