"""The merge action and status check agree on the latest reviewer sign-off."""

from unittest.mock import patch

import pytest

from crew import tools


@pytest.mark.parametrize(
    ("verdicts", "blocker"),
    [
        (["approved", "changes_requested"], "latest reviewer sign-off is not approved"),
        ([], "no reviewer sign-off recorded"),
        (None, "reviewer sign-off ledger is unreadable"),
        (["changes_requested", "approved"], None),
    ],
)
def test_merge_requires_latest_approval_for_requested_pr(mock_gh, verdicts, blocker):
    repo = "warni001/agent-crew"
    with patch("crew.ledger.Ledger.sign_offs", autospec=True) as sign_offs:
        if verdicts is None:
            sign_offs.side_effect = OSError("ledger unavailable")
        else:
            sign_offs.return_value = [
                {"pr_number": 42, "verdict": verdict} for verdict in verdicts
            ]

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
        assert [call.args[1:] for call in sign_offs.call_args_list] == [(42,), (42,)]
