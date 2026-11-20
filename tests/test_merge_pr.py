"""The merge action and status check agree on the latest reviewer sign-off."""

import pytest

from crew import tools
from crew.ledger import Ledger


@pytest.mark.parametrize(
    ("verdicts", "blocker"),
    [
        (["approved", "changes_requested"], "latest reviewer sign-off is not approved"),
        ([], "no reviewer sign-off recorded"),
        (None, "reviewer sign-off ledger is unreadable"),
        (["changes_requested", "approved"], None),
    ],
)
def test_merge_requires_latest_approval_for_requested_pr(
    monkeypatch, tmp_path, mock_gh, verdicts, blocker
):
    repo = "warni001/agent-crew"
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("CREW_LEDGER_PATH", str(path))
    ledger = Ledger(path)
    if verdicts is None:
        path.write_text("not JSON\n", encoding="utf-8")
    else:
        for verdict in verdicts:
            ledger.record("review_sign_off", pr_number=42, verdict=verdict)
        ledger.record("review_sign_off", pr_number=43, verdict="approved")

    status = tools.check_review_sign_off(42)
    if blocker:
        assert status.startswith("NO:") and blocker in status
        with pytest.raises(RuntimeError, match=blocker):
            tools.merge_pr(repo, 42)
        assert mock_gh == []
    else:
        assert status.startswith("YES:")
        assert tools.merge_pr(repo, 42) == "{}"
        assert mock_gh == [["gh", "pr", "merge", "42", "--repo", repo]]


def test_merge_without_ledger_does_not_call_gh(monkeypatch, tmp_path, mock_gh):
    monkeypatch.setenv("CREW_LEDGER_PATH", str(tmp_path / "missing.jsonl"))
    with pytest.raises(RuntimeError, match="no reviewer sign-off recorded"):
        tools.merge_pr("warni001/agent-crew", 42)
    assert mock_gh == []
