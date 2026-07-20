"""Unit tests for env_config.py — list/set env vars this service owns.

Uses tmp_path for the .env file so no real filesystem mutation of the repo's
own .env happens during the test run.
"""

import os

import pytest

import env_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for spec in env_config.OWNED_VARS:
        monkeypatch.delenv(spec["key"], raising=False)


def test_list_env_vars_reports_not_configured_when_unset():
    rows = {row["key"]: row for row in env_config.list_env_vars()}
    assert rows["LLM_PROVIDER"]["configured"] is False
    assert "hint" not in rows["LLM_PROVIDER"]


def test_list_env_vars_masks_secret_hint(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-abcd1234")
    rows = {row["key"]: row for row in env_config.list_env_vars()}
    row = rows["OPENAI_API_KEY"]
    assert row["secret"] is True
    assert row["configured"] is True
    assert row["hint"] == "…1234"
    assert "sk-abcd1234" not in str(row)


def test_list_env_vars_shows_full_value_for_non_secret(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    rows = {row["key"]: row for row in env_config.list_env_vars()}
    assert rows["LLM_PROVIDER"]["hint"] == "ollama"


def test_set_env_var_rejects_unknown_key(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("")
    with pytest.raises(ValueError):
        env_config.set_env_var("SOME_RANDOM_VAR", "x", env_path)


def test_set_env_var_rejects_invalid_llm_provider_value(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("")
    with pytest.raises(ValueError):
        env_config.set_env_var("LLM_PROVIDER", "definitely-not-a-real-provider", env_path)
    assert "LLM_PROVIDER" not in env_path.read_text()


def test_set_env_var_accepts_valid_llm_provider_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("")
    env_config.set_env_var("LLM_PROVIDER", "openai_compatible", env_path)
    assert os.environ["LLM_PROVIDER"] == "openai_compatible"


def test_set_env_var_persists_and_updates_process_env(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("LLM_PROVIDER=ollama\n")

    env_config.set_env_var("LLM_PROVIDER", "openai_compatible", env_path)

    assert os.environ["LLM_PROVIDER"] == "openai_compatible"
    assert "openai_compatible" in env_path.read_text()
