"""Queued tasks close their original issue without changing feedback or exit codes."""

from types import SimpleNamespace
from unittest import mock

import pytest

from conftest import require_crew

REPO = "warni001/agent-crew"
TASK = "Fix X\n\nDetails"


@pytest.fixture
def run_task(tmp_path, monkeypatch):
    main = require_crew("__main__")
    ledger = require_crew("ledger").Ledger(tmp_path / "ledger.jsonl")
    config = SimpleNamespace(repo=REPO, ledger_path=tmp_path / "ledger.jsonl")
    monkeypatch.setattr(main, "build_workflow", mock.Mock())

    def run(verdict, issue_number):
        async def workflow(*args):
            if verdict == "error":
                raise RuntimeError("workflow failed")
            if verdict:
                ledger.record("review_sign_off", pr_number=7, verdict=verdict, notes="")
            return ["Workflow output"]

        monkeypatch.setattr(main, "run_task", workflow)
        code = main.run_one_task(config, ledger, TASK, issue_number)
        return code, ledger.entries()[-1]["event"]

    return run


@pytest.mark.parametrize(
    "verdict,outcome,event,code,feedback_title",
    [
        ("approved", "done", "task_finished", 0, "Done"),
        ("changes_requested", "stalled", "task_stalled", 1, "Stalled"),
        (None, "stalled", "task_stalled", 1, "Stalled"),
        ("error", "failed", "task_failed", 1, None),
    ],
)
def test_closes_source_issue(run_task, mock_gh, verdict, outcome, event, code, feedback_title):
    assert run_task(verdict, 42) == (code, event)
    close, *feedback = mock_gh
    assert close == [
        "gh", "issue", "close", "42", "--repo", REPO,
        "--comment", f"Crew outcome: {outcome}.",
    ]
    if feedback_title is None:
        assert feedback == []
    else:
        assert len(feedback) == 1
        assert feedback[0][:7] == [
            "gh", "issue", "create", "--repo", REPO, "--title",
            f"[crew] {feedback_title}: {TASK}",
        ]


def test_no_issue_number_skips_close(run_task, mock_gh):
    assert run_task("approved", None) == (0, "task_finished")
    assert [cmd[2] for cmd in mock_gh] == ["create"]


@pytest.mark.parametrize("failing_command", ["close", "create"])
def test_close_and_feedback_fail_independently(run_task, monkeypatch, failing_command):
    def gh(cmd, **kwargs):
        return mock.Mock(returncode=int(cmd[2] == failing_command), stdout="", stderr="")

    run_gh = mock.Mock(side_effect=gh)
    monkeypatch.setattr("subprocess.run", run_gh)
    assert run_task("approved", 42) == (0, "task_finished")
    assert [call.args[0][2] for call in run_gh.call_args_list] == ["close", "create"]


def test_close_rejects_other_repos(mock_gh):
    feedback = require_crew("feedback")
    with pytest.raises(ValueError, match="scope lock"):
        feedback.close_task_issue("other/repo", 42, "done")
    assert mock_gh == []
