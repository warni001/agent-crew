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


def test_stalled_task_is_not_retried(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    config = make_config(config_mod)
    ledger = make_ledger(ledger_mod, tmp_path)
    ledger.record("task_finished", task=main_mod.DEFAULT_TASK)
    ledger.record("task_stalled", task="Capped job", limit="round")
    queued = [issue(1, "Capped job")]
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


def test_account_endpoint_derives(config_mod):
    """The bare account endpoint is a valid derivation source now:
    it names the same account, so it resolves to the classic
    endpoint. (It was rejected before the Nov 19 endpoint switch,
    when the Foundry project endpoint was the only accepted shape.)"""
    config = make_config(
        config_mod, FOUNDRY_PROJECT_ENDPOINT=ACCOUNT_ENDPOINT
    )
    config.validate()  # must not raise
    assert (
        config.resolve_azure_openai_endpoint()
        == "https://foundry-gpt-vaxvu3c2.openai.azure.com"
    )


# --- poll interval and one-shot mode ---


def test_poll_seconds_default_is_60(main_mod, monkeypatch):
    monkeypatch.delenv("CREW_POLL_SECONDS", raising=False)
    assert main_mod.poll_seconds() == 60


def test_poll_seconds_env_override(main_mod, monkeypatch):
    monkeypatch.setenv("CREW_POLL_SECONDS", "30")
    assert main_mod.poll_seconds() == 30


def _record_sign_off(ledger_mod, path, verdict="approved", pr_number=1, notes=""):
    """A reviewer sign-off in the local sign-off ledger file."""
    ledger_mod.Ledger(path).record(
        "review_sign_off", pr_number=pr_number, verdict=verdict, notes=notes
    )


def _run_task_recording_sign_off(ledger_mod, path, outputs, verdict="approved", **kw):
    """A mocked run_task that records a sign-off mid-run, like the
    real reviewer tool does, then returns the workflow outputs."""
    async def _run(workflow, task, config):
        _record_sign_off(ledger_mod, path, verdict=verdict, **kw)
        return outputs
    return mock.AsyncMock(side_effect=_run)


def test_stalled_issue_payload(feedback_mod):
    payload = feedback_mod.stalled_issue_payload("Fix X", "hit the cap")
    assert payload["title"] == "[crew] Stalled: Fix X"
    assert "not done" in payload["body"]
    with pytest.raises(ValueError):
        feedback_mod.stalled_issue_payload("", "hit the cap")
    with pytest.raises(ValueError):
        feedback_mod.stalled_issue_payload("Fix X", "")


def test_changes_requested_sign_off_records_stalled(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    """A reviewer changes_requested settles the task as stalled with
    the review notes — never as finished."""
    path = str(tmp_path / "ledger.jsonl")
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    ledger = make_ledger(ledger_mod, tmp_path)
    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    monkeypatch.setattr(
        main_mod, "run_task",
        _run_task_recording_sign_off(
            ledger_mod, path, ["merged nothing"], verdict="changes_requested",
            pr_number=7, notes="fix the test",
        ),
    )
    stalled = mock.Mock(return_value="https://example/2")
    monkeypatch.setattr(main_mod, "report_stalled", stalled)
    done = mock.Mock()
    monkeypatch.setattr(main_mod, "report_done", done)
    assert main_mod.run_one_task(config, ledger, "do the thing") == 1
    task_events = [e for e in ledger.entries() if e["event"].startswith("task_")]
    assert [e["event"] for e in task_events] == ["task_started", "task_stalled"]
    assert task_events[1]["reason"] == "review"
    stalled.assert_called_once()
    assert stalled.call_args.kwargs["reason"] == "review"
    assert "fix the test" in stalled.call_args.args[2]
    done.assert_not_called()


@pytest.mark.parametrize("failure", ["initial", "final"])
def test_unreadable_sign_off_ledger_stalls(
    main_mod, config_mod, ledger_mod, tmp_path, monkeypatch, failure,
):
    ledger = make_ledger(ledger_mod, tmp_path)
    path = str(ledger.path)
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    error = OSError("unreadable sign-off ledger")
    reads = [error] if failure == "initial" else [[], error]
    monkeypatch.setattr(ledger_mod.Ledger, "sign_offs", mock.Mock(side_effect=reads))
    monkeypatch.setattr(main_mod, "build_workflow", mock.Mock())
    monkeypatch.setattr(
        main_mod, "run_task", _run_task_recording_sign_off(ledger_mod, path, ["output"]),
    )
    stalled = mock.Mock()
    done = mock.Mock()
    monkeypatch.setattr(main_mod, "report_stalled", stalled)
    monkeypatch.setattr(main_mod, "report_done", done)

    assert main_mod.run_one_task(config, ledger, "task") == 1
    events = [e for e in ledger.entries() if e["event"].startswith("task_")]
    assert [e["event"] for e in events] == ["task_started", "task_stalled"]
    assert events[-1]["reason"] == "review_unavailable"
    stalled.assert_called_once()
    assert stalled.call_args.kwargs["reason"] == "review_unavailable"
    assert "unknown (sign-off ledger unreadable)" in stalled.call_args.args[2]
    done.assert_not_called()


def test_changes_requested_plus_approved_finishes(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    """A changes_requested on one PR does not stall a sweep that
    approved another: per-PR blockers are not task blockers."""
    path = str(tmp_path / "ledger.jsonl")
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    ledger = make_ledger(ledger_mod, tmp_path)

    async def _run(workflow, task, config):
        _record_sign_off(
            ledger_mod, path, verdict="changes_requested",
            pr_number=7, notes="fix the test",
        )
        _record_sign_off(
            ledger_mod, path, verdict="approved", pr_number=8, notes="",
        )
        return ["merged PR #8"]

    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    monkeypatch.setattr(main_mod, "run_task", _run)
    stalled = mock.Mock(return_value="https://example/2")
    monkeypatch.setattr(main_mod, "report_stalled", stalled)
    monkeypatch.setattr(main_mod, "report_done", lambda *a, **k: "https://example/1")
    assert main_mod.run_one_task(config, ledger, "do the thing") == 0
    task_events = [e for e in ledger.entries() if e["event"].startswith("task_")]
    assert [e["event"] for e in task_events] == ["task_started", "task_finished"]
    stalled.assert_not_called()


def test_no_sign_off_records_stalled(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    """No review recorded means nothing shipped: the run stalls
    instead of claiming a finish with zero PRs (the Nov 19
    hollow-done mistake)."""
    path = str(tmp_path / "ledger.jsonl")
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    ledger = make_ledger(ledger_mod, tmp_path)
    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    monkeypatch.setattr(main_mod, "run_task", mock.AsyncMock(return_value=["done"]))
    stalled = mock.Mock(return_value="https://example/2")
    monkeypatch.setattr(main_mod, "report_stalled", stalled)
    done = mock.Mock()
    monkeypatch.setattr(main_mod, "report_done", done)
    assert main_mod.run_one_task(config, ledger, "do the thing") == 1
    task_events = [e for e in ledger.entries() if e["event"].startswith("task_")]
    assert [e["event"] for e in task_events] == ["task_started", "task_stalled"]
    assert task_events[1]["reason"] == "no_review"
    assert stalled.call_args.kwargs["reason"] == "no_review"
    done.assert_not_called()


def test_approved_sign_off_finishes(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    """An approved sign-off during the run lets it finish."""
    path = str(tmp_path / "ledger.jsonl")
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    ledger = make_ledger(ledger_mod, tmp_path)
    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    monkeypatch.setattr(
        main_mod, "run_task",
        _run_task_recording_sign_off(ledger_mod, path, ["merged PR #1"]),
    )
    monkeypatch.setattr(main_mod, "report_done", lambda *a, **k: "https://example/1")
    assert main_mod.run_one_task(config, ledger, "do the thing") == 0
    task_events = [e for e in ledger.entries() if e["event"].startswith("task_")]
    assert [e["event"] for e in task_events] == ["task_started", "task_finished"]


def test_stale_sign_off_before_run_is_ignored(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    """A changes_requested from an earlier run must not stall this
    one: only sign-offs recorded during this run count."""
    path = str(tmp_path / "ledger.jsonl")
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    ledger = make_ledger(ledger_mod, tmp_path)
    _record_sign_off(ledger_mod, path, verdict="changes_requested", pr_number=3, notes="old")
    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    monkeypatch.setattr(
        main_mod, "run_task",
        _run_task_recording_sign_off(ledger_mod, path, ["merged PR #4"]),
    )
    monkeypatch.setattr(main_mod, "report_done", lambda *a, **k: "https://example/1")
    assert main_mod.run_one_task(config, ledger, "do the thing") == 0


def test_ledger_failure_does_not_kill_run(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    """A ledger PermissionError (the live crash-loop cause) warns and the
    task still runs: the ledger is an audit trail, not the work itself.
    The run still needs its approved sign-off to finish — recorded
    here into the sign-off file the gate reads, like the real
    reviewer tool does."""
    path = str(tmp_path / "ledger.jsonl")
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    ledger = mock.Mock()
    ledger.record.side_effect = PermissionError("Permission denied")
    ledger.entries.return_value = []
    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    monkeypatch.setattr(
        main_mod, "run_task",
        _run_task_recording_sign_off(ledger_mod, path, ["done"]),
    )
    monkeypatch.setattr(main_mod, "report_done", lambda *a, **k: "https://example/1")
    assert main_mod.run_one_task(config, ledger, "do the thing") == 0
    run_task = main_mod.run_task
    run_task.assert_awaited_once()


def test_main_explicit_task_runs_once(main_mod, ledger_mod, tmp_path, monkeypatch):
    path = str(tmp_path / "ledger.jsonl")
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", PROJECT_ENDPOINT)
    monkeypatch.setenv("KEY_VAULT_URL", "https://kv-example.vault.azure.net/")
    monkeypatch.setenv("CREW_LEDGER_PATH", path)
    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    run_task = _run_task_recording_sign_off(ledger_mod, path, ["done"])
    monkeypatch.setattr(main_mod, "run_task", run_task)
    monkeypatch.setattr(main_mod, "report_done", lambda *a, **k: "https://example/1")
    assert main_mod.main(["do the thing"]) == 0
    run_task.assert_awaited_once()
    assert run_task.await_args.args[1] == "do the thing"


# --- settlement closes the original issue and tags it on stalls ---


def _stall_mocks(main_mod, monkeypatch):
    """Mock the three feedback calls a stall makes; return them."""
    stalled = mock.Mock(return_value="https://example/stalled")
    close = mock.Mock()
    tag = mock.Mock()
    monkeypatch.setattr(main_mod, "report_stalled", stalled)
    monkeypatch.setattr(main_mod, "close_task_issue", close)
    monkeypatch.setattr(main_mod, "tag_task_stalled", tag)
    monkeypatch.setattr(main_mod, "report_done", mock.Mock())
    return stalled, close, tag


def test_stalled_run_closes_and_tags_original_issue(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    path = str(tmp_path / "ledger.jsonl")
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    ledger = make_ledger(ledger_mod, tmp_path)
    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    monkeypatch.setattr(main_mod, "run_task", mock.AsyncMock(return_value=["done"]))
    stalled, close, tag = _stall_mocks(main_mod, monkeypatch)
    assert main_mod.run_one_task(config, ledger, "do the thing", issue_number=42) == 1
    stalled.assert_called_once()
    summary = stalled.call_args.args[2]
    close.assert_called_once_with(config.repo, 42, f"Stalled: no_review\n\n{summary}")
    tag.assert_called_once_with("warni001/agent-crew", 42)
    main_mod.report_done.assert_not_called()


def test_review_stalled_run_closes_with_review_reason(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    path = str(tmp_path / "ledger.jsonl")
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    ledger = make_ledger(ledger_mod, tmp_path)
    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    monkeypatch.setattr(
        main_mod, "run_task",
        _run_task_recording_sign_off(
            ledger_mod, path, ["merged nothing"], verdict="changes_requested",
            pr_number=7, notes="fix the test",
        ),
    )
    stalled, close, tag = _stall_mocks(main_mod, monkeypatch)
    assert main_mod.run_one_task(config, ledger, "do the thing", issue_number=42) == 1
    stalled.assert_called_once()
    summary = stalled.call_args.args[2]
    assert "fix the test" in summary
    close.assert_called_once_with(config.repo, 42, f"Stalled: review\n\n{summary}")
    tag.assert_called_once_with(config.repo, 42)


def test_review_unavailable_stall_closes_source_issue_once(
    main_mod, config_mod, ledger_mod, tmp_path, monkeypatch
):
    """An unreadable review ledger stalls through the close-and-label
    helper: the source issue is closed once with the stall reason and
    summary, keeps the stalled label, and Done is never reported."""
    ledger = make_ledger(ledger_mod, tmp_path)
    path = str(ledger.path)
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    monkeypatch.setattr(
        ledger_mod.Ledger, "sign_offs",
        mock.Mock(side_effect=OSError("unreadable sign-off ledger")),
    )
    monkeypatch.setattr(main_mod, "build_workflow", mock.Mock())
    monkeypatch.setattr(
        main_mod, "run_task",
        _run_task_recording_sign_off(ledger_mod, path, ["output"]),
    )
    stalled, close, tag = _stall_mocks(main_mod, monkeypatch)
    assert main_mod.run_one_task(config, ledger, "task", issue_number=42) == 1
    events = [e for e in ledger.entries() if e["event"].startswith("task_")]
    assert [e["event"] for e in events] == ["task_started", "task_stalled"]
    assert events[-1]["reason"] == "review_unavailable"
    stalled.assert_called_once()
    assert stalled.call_args.kwargs["reason"] == "review_unavailable"
    summary = stalled.call_args.args[2]
    close.assert_called_once_with(
        config.repo, 42, f"Stalled: review_unavailable\n\n{summary}"
    )
    tag.assert_called_once_with(config.repo, 42)
    main_mod.report_done.assert_not_called()


def test_stalled_run_without_issue_number_skips_marking(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    """A one-shot run has no original issue: stall posts the stalled
    issue and nothing else."""
    path = str(tmp_path / "ledger.jsonl")
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    ledger = make_ledger(ledger_mod, tmp_path)
    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    monkeypatch.setattr(main_mod, "run_task", mock.AsyncMock(return_value=["done"]))
    stalled, close, tag = _stall_mocks(main_mod, monkeypatch)
    assert main_mod.run_one_task(config, ledger, "do the thing") == 1
    stalled.assert_called_once()
    close.assert_not_called()
    tag.assert_not_called()


@pytest.mark.parametrize("issue_number", [42, None])
@pytest.mark.parametrize("failed", [False, True])
def test_done_or_failed_run_closes_only_source_issue(
    main_mod, config_mod, ledger_mod, tmp_path, monkeypatch, issue_number, failed
):
    path = str(tmp_path / "ledger.jsonl")
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    ledger = make_ledger(ledger_mod, tmp_path)
    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    if failed:
        run = mock.AsyncMock(side_effect=RuntimeError("workflow failed"))
        comment = "Failed: the workflow raised an exception."
        event = "task_failed"
    else:
        run = _run_task_recording_sign_off(ledger_mod, path, ["merged PR #1"])
        comment = "Done: the task finished."
        event = "task_finished"
    monkeypatch.setattr(main_mod, "run_task", run)
    stalled, close, tag = _stall_mocks(main_mod, monkeypatch)
    assert main_mod.run_one_task(config, ledger, "do the thing", issue_number) == int(failed)
    assert ledger.entries()[-1]["event"] == event
    if issue_number is None:
        close.assert_not_called()
    else:
        close.assert_called_once_with(config.repo, issue_number, comment)
    if failed:
        main_mod.report_done.assert_not_called()
    else:
        main_mod.report_done.assert_called_once()
    stalled.assert_not_called()
    tag.assert_not_called()


def test_stall_tolerates_close_and_label_failure(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch, caplog):
    """A close or label failure must not fail settlement: the run
    still settles stalled and exits 1."""
    path = str(tmp_path / "ledger.jsonl")
    config = make_config(config_mod, CREW_LEDGER_PATH=path)
    ledger = make_ledger(ledger_mod, tmp_path)
    monkeypatch.setattr(main_mod, "build_workflow", lambda config: mock.Mock())
    monkeypatch.setattr(main_mod, "run_task", mock.AsyncMock(return_value=["done"]))
    stalled, close, tag = _stall_mocks(main_mod, monkeypatch)
    close.side_effect = RuntimeError("gh down")
    tag.side_effect = RuntimeError("gh down")
    assert main_mod.run_one_task(config, ledger, "do the thing", issue_number=42) == 1
    stalled.assert_called_once()
    close.assert_called_once()
    tag.assert_called_once_with(config.repo, 42)
    assert "could not close task issue #42" in caplog.text
    task_events = [e for e in ledger.entries() if e["event"].startswith("task_")]
    assert [e["event"] for e in task_events] == ["task_started", "task_stalled"]


def test_next_task_issue_returns_number(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    config = make_config(config_mod)
    ledger = make_ledger(ledger_mod, tmp_path)
    ledger.record("task_finished", task=main_mod.DEFAULT_TASK)
    queued = [issue(1, "Old job"), issue(2, "New job")]
    monkeypatch.setattr(main_mod, "list_done_tasks", lambda repo: set())
    monkeypatch.setattr(main_mod, "list_task_issues", lambda repo: queued)
    picked = main_mod.next_task_issue(config, ledger)
    assert picked == {"task": "Old job", "number": 1}


def test_next_task_issue_default_task_has_no_number(main_mod, config_mod, ledger_mod, tmp_path, monkeypatch):
    config = make_config(config_mod)
    ledger = make_ledger(ledger_mod, tmp_path)
    monkeypatch.setattr(main_mod, "list_done_tasks", lambda repo: set())
    picked = main_mod.next_task_issue(config, ledger)
    assert picked == {"task": main_mod.DEFAULT_TASK, "number": None}
