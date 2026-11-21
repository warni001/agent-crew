"""merge_pr must pass an explicit merge strategy to gh, and must agree
with the latest reviewer sign-off.

Regression (Nov 20, 2026): the merger had an approved PR (#36) but
could not merge it — `gh pr merge` in non-interactive mode requires
an explicit --merge / --rebase / --squash flag, and merge_pr passed
none, so the command failed and the PR stayed open.

The merge action and status check also agree on the latest reviewer
sign-off: a newer rejection blocks the merge even when an earlier
approval exists, and a missing or unreadable ledger blocks it too.
"""

from __future__ import annotations

from unittest import mock

import pytest
from conftest import require_crew

from crew import tools
from crew.ledger import Ledger


def _fake_run(monkeypatch, calls):
    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        result = mock.Mock()
        result.returncode = 0
        result.stdout = "Merged pull request #36"
        result.stderr = ""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)


def test_merge_pr_passes_explicit_merge_strategy(monkeypatch, tmp_path):
    tools_mod = require_crew("tools")
    # merge_pr refuses without an approved latest sign-off, so record
    # one for PR #36 first; the test is about the gh call, not gating.
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("CREW_LEDGER_PATH", str(path))
    Ledger(path).record("review_sign_off", pr_number=36, verdict="approved")
    calls: list[list[str]] = []
    _fake_run(monkeypatch, calls)
    tools_mod.merge_pr("warni001/agent-crew", 36)
    assert calls == [
        ["gh", "pr", "merge", "36", "--merge", "--repo", "warni001/agent-crew"]
    ]


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
        assert mock_gh == [["gh", "pr", "merge", "42", "--merge", "--repo", repo]]


def test_merge_without_ledger_does_not_call_gh(monkeypatch, tmp_path, mock_gh):
    monkeypatch.setenv("CREW_LEDGER_PATH", str(tmp_path / "missing.jsonl"))
    with pytest.raises(RuntimeError, match="no reviewer sign-off recorded"):
        tools.merge_pr("warni001/agent-crew", 42)
    assert mock_gh == []


def test_boolean_pr_number_does_not_match_pr_1(monkeypatch, tmp_path, mock_gh):
    """A malformed approval with pr_number true must not count for
    PR #1: True == 1 in Python, so a plain equality match would let
    it through the merge gate."""
    repo = "warni001/agent-crew"
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("CREW_LEDGER_PATH", str(path))
    Ledger(path).record("review_sign_off", pr_number=True, verdict="approved")
    status = tools.check_review_sign_off(1)
    assert status.startswith("NO:") and "no reviewer sign-off recorded" in status
    with pytest.raises(RuntimeError, match="no reviewer sign-off recorded"):
        tools.merge_pr(repo, 1)
    assert mock_gh == []
