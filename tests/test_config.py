"""Config: environment only, no secrets in code.

Contract under test (see docs/crew-instructions.md, "Module contract")::

    crew.config.load_config(env=None) -> Config
    crew.config.ConfigError  (subclass of ValueError)
    Config fields: key_vault_url, foundry_gpt_endpoint, github_token

Rules: all three values come from the environment (KEY_VAULT_URL,
FOUNDRY_GPT_ENDPOINT, GITHUB_TOKEN). A missing variable is a hard
error naming the variable — never a silent default, never a fallback
to a file, never a literal secret in code. The loader must not read
``.env`` files: secrets live in the Key Vault / the environment only.

Blocked until ``crew/config.py`` is merged; these tests skip until then.
"""

from __future__ import annotations

import os

import pytest

from conftest import require_crew

ENV_VARS = ("KEY_VAULT_URL", "FOUNDRY_GPT_ENDPOINT", "GITHUB_TOKEN")


@pytest.fixture
def config_mod():
    return require_crew("config")


def test_load_config_reads_all_three_vars(config_mod, clean_env):
    cfg = config_mod.load_config(env=dict(os.environ))
    assert cfg.key_vault_url == "https://kv-agentcrew-vaxvu3c2.vault.azure.net/"
    assert cfg.foundry_gpt_endpoint == "https://foundry-gpt-vaxvu3c2.cognitiveservices.azure.com/"
    assert cfg.github_token == "ghp_test-token-placeholder"


def test_load_config_defaults_to_os_environ(config_mod, clean_env):
    cfg = config_mod.load_config()
    assert cfg.key_vault_url.startswith("https://")
    assert "foundry-gpt-vaxvu3c2" in cfg.foundry_gpt_endpoint


@pytest.mark.parametrize("missing", ENV_VARS)
def test_load_config_missing_var_is_hard_error(config_mod, clean_env, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(config_mod.ConfigError) as excinfo:
        config_mod.load_config()
    assert missing in str(excinfo.value)


def test_load_config_error_is_value_error(config_mod):
    assert issubclass(config_mod.ConfigError, ValueError)


def test_load_config_rejects_empty_string(config_mod, clean_env, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_config()


def test_load_config_does_not_read_dotenv(config_mod, tmp_path, monkeypatch):
    """A .env file sitting in the cwd must not supply config values."""
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "KEY_VAULT_URL=https://evil.example/\n"
        "FOUNDRY_GPT_ENDPOINT=https://evil.example/\n"
        "GITHUB_TOKEN=ghp_from_dotenv\n"
    )
    monkeypatch.chdir(tmp_path)
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_config()


def test_from_env_ledger_blob_url_defaults_empty(config_mod, clean_env):
    cfg = config_mod.CrewConfig.from_env(env=dict(os.environ))
    assert cfg.ledger_blob_url == ""


def test_from_env_ledger_blob_url_reads_env(config_mod, clean_env, monkeypatch):
    monkeypatch.setenv(
        "CREW_LEDGER_BLOB_URL",
        "https://st-test.blob.core.windows.net/ledger/crew-ledger.jsonl",
    )
    cfg = config_mod.CrewConfig.from_env()
    assert (
        cfg.ledger_blob_url
        == "https://st-test.blob.core.windows.net/ledger/crew-ledger.jsonl"
    )


def test_no_secret_literals_in_crew_config_source(config_mod):
    """The config module itself must not contain a literal token/secret."""
    import inspect

    src = inspect.getsource(config_mod)
    for literal in ("ghp_", "gho_", "sk-"):
        assert literal not in src
    assert 'open(".env' not in src and "open('.env" not in src
    assert "load_dotenv" not in src
