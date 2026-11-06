"""No test can read the real .env or reach the model service.

This runs before any test module (or any provenance module) is imported:

- LLM_API_KEY is an explicit dummy and GROQ_BASE_URL a closed local port, so anything
  that does build a real client fails fast without leaving this machine. Subprocesses
  inherit both, and load_dotenv never overrides a variable that is already set.
- load_dotenv does nothing in the test process, so a CLI's main() or GroqLLM.from_env()
  cannot pull the real file into the environment, even after a test deletes the key.

tests/unit/test_llm_isolation.py checks all of this.
"""

from __future__ import annotations

import os

import dotenv

DUMMY_LLM_KEY = "test-dummy-key-not-a-real-credential"
UNREACHABLE_BASE_URL = "http://127.0.0.1:9"

os.environ["LLM_API_KEY"] = DUMMY_LLM_KEY
os.environ["GROQ_BASE_URL"] = UNREACHABLE_BASE_URL


def _no_dotenv(*_args: object, **_kwargs: object) -> bool:
    return False


dotenv.load_dotenv = _no_dotenv
