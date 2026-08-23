# tests/test_env_vars.py — env_vars.find_env_file()'s MCP_ENV_FILE handling.
#
# The bug this guards against: in the real Docker deployment, find_dotenv()
# finds nothing inside the container (env_file: in docker-compose.yml only
# injects values into os.environ, it never copies the .env file itself into
# the container), so every PUT /config/env used to 400 with "no .env file
# found". MCP_ENV_FILE gives it a dedicated, always-creatable writable path
# instead — see docker-compose.yml's mcp-server service and main.py's
# startup load of it.

from pathlib import Path

import env_vars


def test_find_env_file_prefers_mcp_env_file_when_set(tmp_path, monkeypatch):
    target = tmp_path / "runtime" / ".env"
    monkeypatch.setenv("MCP_ENV_FILE", str(target))

    result = env_vars.find_env_file()

    assert result == str(target)


def test_find_env_file_creates_missing_file_and_parent_dir(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "dir" / ".env"
    assert not target.parent.exists()
    monkeypatch.setenv("MCP_ENV_FILE", str(target))

    env_vars.find_env_file()

    assert target.exists()
    assert target.read_text() == ""


def test_find_env_file_does_not_truncate_existing_file(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    target.write_text("SEARXNG_BASE_URL=http://x:8080\n")
    monkeypatch.setenv("MCP_ENV_FILE", str(target))

    env_vars.find_env_file()

    assert "SEARXNG_BASE_URL" in target.read_text()


def test_find_env_file_falls_back_to_find_dotenv_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_ENV_FILE", raising=False)
    fake_dotenv = tmp_path / ".env"
    fake_dotenv.write_text("")
    monkeypatch.setattr(env_vars, "find_dotenv", lambda usecwd=True: str(fake_dotenv))

    result = env_vars.find_env_file()

    assert result == str(fake_dotenv)


def test_set_env_var_writes_to_mcp_env_file_path(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    monkeypatch.setenv("MCP_ENV_FILE", str(target))

    env_path = env_vars.find_env_file()
    env_vars.set_env_var("SEARXNG_BASE_URL", "http://x:9090", env_path)

    from dotenv import dotenv_values

    assert dotenv_values(str(target))["SEARXNG_BASE_URL"] == "http://x:9090"
    assert Path(target).exists()
