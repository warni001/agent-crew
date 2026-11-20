"""Settlement updates the original queue issue without changing the run result."""

from types import SimpleNamespace
from unittest import mock

import pytest

from conftest import require_crew


@pytest.mark.parametrize(
    "verdict,event,reason,comment_prefix,code",
    [
        ("approved", "task_finished", None, "Done:", 0),
        ("changes_requested", "task_stalled", "review", "Stalled: review", 1),
        (None, "task_stalled", "no_review", "Stalled: no_review", 1),
        ("error", "task_failed", None, "Failed:", 1),
    ],
)
@pytest.mark.parametrize("mode", ["queued", "issue-less", "feedback-error"])
def test_settlement(verdict, event, reason, comment_prefix, code, mode, tmp_path, monkeypatch):
    main = require_crew("__main__")
    repo = "warni001/agent-crew"
    config = SimpleNamespace(repo=repo, ledger_path=str(tmp_path / "ledger.jsonl"))
    ledger = main.Ledger(config.ledger_path)
    issue_number = None if mode == "issue-less" else 42

    async def run(*args):
        if verdict == "error":
            raise RuntimeError("workflow failed")
        if verdict:
            ledger.record("review_sign_off", pr_number=7, verdict=verdict)
        return ["workflow output"]

    monkeypatch.setattr(main, "build_workflow", mock.Mock())
    monkeypatch.setattr(main, "run_task", run)
    monkeypatch.setattr(main, "format_run_summary", mock.Mock(return_value="run summary"))
    feedback = mock.Mock()
    for name in ("report_done", "report_stalled", "comment_on_task_issue",
                 "tag_task_stalled", "close_task_issue"):
        method = getattr(feedback, name)
        if mode == "feedback-error":
            method.side_effect = RuntimeError("gh down")
        monkeypatch.setattr(main, name, method)

    assert main.run_one_task(config, ledger, "task", issue_number=issue_number) == code
    records = [e for e in ledger.entries() if e["event"].startswith("task_")]
    assert [e["event"] for e in records] == ["task_started", event]
    assert records[-1].get("reason") == reason

    if event == "task_finished":
        feedback.report_done.assert_called_once_with(repo, "task", "run summary")
    else:
        feedback.report_done.assert_not_called()
    if reason:
        feedback.report_stalled.assert_called_once_with(repo, "task", "run summary", reason=reason)
    else:
        feedback.report_stalled.assert_not_called()

    if issue_number is None:
        feedback.comment_on_task_issue.assert_not_called()
        feedback.tag_task_stalled.assert_not_called()
        feedback.close_task_issue.assert_not_called()
        return

    feedback.comment_on_task_issue.assert_called_once()
    comment_repo, comment_number, body = feedback.comment_on_task_issue.call_args.args
    assert (comment_repo, comment_number) == (repo, issue_number)
    assert body.startswith(comment_prefix)
    if reason:
        assert body == f"{comment_prefix}\n\nrun summary"
        feedback.tag_task_stalled.assert_called_once_with(repo, issue_number)
    else:
        feedback.tag_task_stalled.assert_not_called()
    feedback.close_task_issue.assert_called_once_with(repo, issue_number)
    assert feedback.mock_calls[-1] == mock.call.close_task_issue(repo, issue_number)


def test_close_task_issue_is_repo_scoped(mock_gh):
    feedback = require_crew("feedback")
    with pytest.raises(ValueError, match="scope lock"):
        feedback.close_task_issue("someone-else/other-repo", 42)
    with pytest.raises(ValueError, match="positive integer"):
        feedback.close_task_issue("warni001/agent-crew", "https://github.com/other/repo/issues/42")
    assert mock_gh == []
    feedback.close_task_issue("warni001/agent-crew", 42)
    assert mock_gh == [
        ["gh", "issue", "close", "42", "--repo", "warni001/agent-crew"]
    ]
