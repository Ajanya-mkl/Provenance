"""Guards for tests/conftest.py: no test reads the real .env or reaches the model service."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from provenance.agent import background, llm
from provenance.agent.llm import GroqLLM, LLMError
from provenance.datasets import map as map_cli
from provenance.datasets import score as score_cli
from provenance.datasets.onboard import PROJECT_ROOT
from tests.conftest import DUMMY_LLM_KEY, UNREACHABLE_BASE_URL


def test_the_environment_holds_the_dummy_key_and_the_unreachable_address():
    assert os.environ["LLM_API_KEY"] == DUMMY_LLM_KEY
    assert os.environ["GROQ_BASE_URL"] == UNREACHABLE_BASE_URL


def test_a_real_client_gets_the_dummy_key_and_the_unreachable_address():
    client = GroqLLM.from_env().client

    assert client.api_key == DUMMY_LLM_KEY
    assert str(client.base_url).startswith(UNREACHABLE_BASE_URL)


def test_every_load_dotenv_in_the_code_is_disabled():
    for module in (llm, background, map_cli, score_cli):
        assert module.load_dotenv("anything") is False, module.__name__


def test_with_the_key_removed_nothing_brings_it_back(monkeypatch):
    # The real .env is not loaded, so the key stays missing.
    monkeypatch.delenv("LLM_API_KEY")

    with pytest.raises(LLMError, match="not set"):
        GroqLLM.from_env()
    assert background.llm_configured() is False
    assert "LLM_API_KEY" not in os.environ


def test_a_subprocess_keeps_the_dummy_key_even_when_it_loads_dotenv():
    # A child runs the real load_dotenv, which must not override what it inherited.
    code = (
        "import os; from dotenv import load_dotenv; load_dotenv(); "
        "print(os.environ['LLM_API_KEY'] == os.environ['EXPECTED'], "
        "os.environ['GROQ_BASE_URL'] == os.environ['EXPECTED_URL'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env={**os.environ, "EXPECTED": DUMMY_LLM_KEY, "EXPECTED_URL": UNREACHABLE_BASE_URL},
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.split() == ["True", "True"]
