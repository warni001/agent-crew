"""Run loop: default task first, then the `crew-task` issue queue.

Contract (see docs/crew-instructions.md): with no task argument the crew
runs its default self-build task first, then polls open issues labelled
`crew-task` every 60 seconds and takes the oldest one it has not already
finished or failed. A GitHub failure during polling warns and retries
instead of crashing the loop. All GitHub access is mocked; no network.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from conftest import require_crew

PROJECT_ENDPOINT = "https://foundry-gpt-vaxvu3c2.services.ai.azure.com/api/projects/crew"
ACCOUNT_ENDPOINT = "https://foundry-gpt-vaxvu3c2.cognitiveservices.azure.com/"


@pytest.fixture
def main_mod():
    return require_crew("__main__")


@pytest.fixture
def feedback_mod():
    return require_crew("feedback")


@pytest.fixture
def config_mod():
    return require_crew("config")


@pytest.fixture
def ledger_mod():
    return require_crew("ledger")


def make_config(config_mod, **overrides):
    env = {
        "FOUNDRY_PROJECT_ENDPOINT": PROJECT_ENDPOINT,
        "KEY_VAULT_URL": "https://kv-example.vault.azure.net/",
        "GITHUB_TOKEN": "token",
    }
    env.update(overrides)
    return config_mod.CrewConfig.from_env(env=env)


def make_ledger(ledger_mod, tmp_path):
    return ledger_mod.Ledger(tmp_path / "ledger.jsonl")


def issue(number, title, body=""):
    task = f"{title}\n\n{body}" if body else title
    return {"number": number, "title": title, "body": body, "task": task}


# --- task_text / queue listing (feedback.py) ---


def test_task_text_joins_title_and_body(feedback_mod):
    assert feedback_mod.task_text("Fix X", "Details here") == "Fix X\n\nDetails here"


def test_task_text_title_only_when_no_body(feedback_mod):
    assert feedback_mod.task_text("Fix X", "") == "Fix X"


def test_list_task_issues_oldest_first(feedback_mod, monkeypatch):
    # gh returns newest first; the crew takes the oldest.
    payload = [
        {"number": 3, "title": "Third", "body": ""},
        {"number": 2, "title": "Second", "body": "b"},
    ]
    proc = mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")
    monkeypatch.setattr(feedback_mod.subprocess, "run", lambda *a, **k: proc)
    rows = feedback_mod.list_task_issues("warni001/agent-crew")
    assert [r["number"] for r in rows] == [2, 3]
    assert rows[0]["task"] == "Second\n\nb"


def test_list_done_tasks_strips_prefix(feedback_mod, monkeypatch):
    payload = [
        {"title": "[crew] Done: Fix X"},
        {"title": "Some other issue"},
    ]
    proc = mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")
    monkeypatch.setattr(feedback_mod.subprocess, "run", lambda *a, **k: proc)
    assert feedback_mod.list_done_tasks("warni001/agent-crew") == {"Fix X"}


def test_list_task_issues_rejects_other_repos(feedback_mod):
    with pytest.raises(ValueError):
        feedback_mod.list_task_issues("someone-else/other-repo")


# --- next_task selection (__main__.py) ---


def test_default_task_first(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    config = make_config(config_mod)
    ledger = make_ledger(ledger_mod, tmp_path)
    monkeypatch.setattr(main_mod, "list_done_tasks", lambda repo: set())
    monkeypatch.setattr(main_mod, "list_task_issues", lambda repo: [])
    assert main_mod.next_task(config, ledger) == main_mod.DEFAULT_TASK


def test_default_task_skipped_when_done(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    config = make_config(config_mod)
    ledger = make_ledger(ledger_mod, tmp_path)
    ledger.record("task_finished", task=main_mod.DEFAULT_TASK)
    queued = [issue(1, "Next job", "do it")]
    monkeypatch.setattr(main_mod, "list_done_tasks", lambda repo: set())
    monkeypatch.setattr(main_mod, "list_task_issues", lambda repo: queued)
    assert main_mod.next_task(config, ledger) == "Next job\n\ndo it"


def test_picks_oldest_unfinished(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    config = make_config(config_mod)
    ledger = make_ledger(ledger_mod, tmp_path)
    ledger.record("task_finished", task=main_mod.DEFAULT_TASK)
    ledger.record("task_finished", task="Old job")
    queued = [issue(1, "Old job"), issue(2, "New job")]
    monkeypatch.setattr(main_mod, "list_done_tasks", lambda repo: set())
    monkeypatch.setattr(main_mod, "list_task_issues", lambda repo: queued)
    assert main_mod.next_task(config, ledger) == "New job"


def test_failed_task_is_not_retried(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    config = make_config(config_mod)
    ledger = make_ledger(ledger_mod, tmp_path)
    ledger.record("task_finished", task=main_mod.DEFAULT_TASK)
    ledger.record("task_failed", task="Broken job", error="boom")
    queued = [issue(1, "Broken job")]
    monkeypatch.setattr(main_mod, "list_done_tasks", lambda repo: set())
    monkeypatch.setattr(main_mod, "list_task_issues", lambda repo: queued)
    assert main_mod.next_task(config, ledger) is None


def test_none_when_queue_empty_and_default_done(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    config = make_config(config_mod)
    ledger = make_ledger(ledger_mod, tmp_path)
    ledger.record("task_finished", task=main_mod.DEFAULT_TASK)
    monkeypatch.setattr(main_mod, "list_done_tasks", lambda repo: set())
    monkeypatch.setattr(main_mod, "list_task_issues", lambda repo: [])
    assert main_mod.next_task(config, ledger) is None


def test_gh_failure_returns_none_not_crash(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    config = make_config(config_mod)
    ledger = make_ledger(ledger_mod, tmp_path)
    ledger.record("task_finished", task=main_mod.DEFAULT_TASK)

    def boom(repo):
        raise RuntimeError("gh down")

    monkeypatch.setattr(main_mod, "list_done_tasks", boom)
    monkeypatch.setattr(main_mod, "list_task_issues", boom)
    assert main_mod.next_task(config, ledger) is None


# --- config: project endpoint required ---


def test_project_endpoint_validates(config_mod):
    config = make_config(config_mod)
    config.validate()  # must not raise


def test_account_endpoint_rejected(config_mod):
    config = make_config(
        config_mod, FOUNDRY_PROJECT_ENDPOINT=ACCOUNT_ENDPOINT
    )
    with pytest.raises(ValueError):
        config.validate()


# --- poll interval and one-shot mode ---


def test_poll_seconds_default_is_60(main_mod, monkeypatch):
    monkeypatch.delenv("CREW_POLL_SECONDS", raising=False)
    assert main_mod.poll_seconds() == 60


def test_poll_seconds_env_override(main_mod, monkeypatch):
    monkeypatch.setenv("CREW_POLL_SECONDS", "30")
    assert main_mod.poll_seconds() == 30


def test_main_explicit_task_runs_once(main_mod, tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", PROJECT_ENDPOINT)
    monkeypatch.setenv("KEY_VAULT_URL", "https://kv-example.vault.azure.net/")
    monkeypatch.setenv("CREW_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    run_task = mock.AsyncMock(return_value=["done"])
    monkeypatch.setattr(main_mod, "run_task", run_task)
    monkeypatch.setattr(main_mod, "report_done", lambda *a, **k: "https://example/1")
    assert main_mod.main(["do the thing"]) == 0
    run_task.assert_awaited_once()
    assert run_task.await_args.args[1] == "do the thing"
