"""Run the console API on localhost only: python -m provenance.api [--port 8000].

There is no login, so the API binds to 127.0.0.1 and has no option to listen on
another interface. Never expose it, or the console, to a network.
"""

from __future__ import annotations

import argparse

import uvicorn
from dotenv import load_dotenv

from provenance.datasets.onboard import PROJECT_ROOT

HOST = "127.0.0.1"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    # Load .env before the app is imported, so /api/health and the Agent card read
    # LLM_MODEL_NAME from it at startup. Variables already set in the shell win.
    load_dotenv(PROJECT_ROOT / ".env")
    uvicorn.run("provenance.api.main:app", host=HOST, port=args.port)


if __name__ == "__main__":
    main()
