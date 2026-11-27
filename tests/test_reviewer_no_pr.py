"""The reviewer must never guess a PR number.

Regression (Nov 27, 2026): the #75 run stalled with no_review
because the builder produced no PR and the reviewer — whose only
context was the builder's refusal — invented PR #1, which does not
exist, and wrote 'changes requested' as text without ever calling
record_sign_off. Its tools all need a PR number, and its
instructions gave it no 'no PR' path. The fix: the reviewer finds
the PR from list_open_prs, never guesses a number, and records
nothing when there is no PR to review.
"""

from __future__ import annotations

from conftest import require_crew


def test_reviewer_finds_pr_from_listing_and_never_guesses():
    roles_mod = require_crew("roles")
    text = roles_mod._REVIEWER_INSTRUCTIONS
    assert "list_open_prs" in text
    assert "never guess one" in text
    assert "no PR to review" in text
    assert "record nothing" in text
    assert "do not call read_pr or record_sign_off" in text


def test_list_open_prs_is_in_reviewer_toolset():
    """The reviewer needs list_open_prs to find the PR itself; the
    builder's response text is not a reliable source for the number
    (the #75 run proved it — the reviewer read a refusal and
    invented PR #1)."""
    tools_mod = require_crew("tools")
    names = [t.name for t in tools_mod.REVIEW_TOOLS]
    assert "list_open_prs" in names
    all_names = [t.name for t in tools_mod.ALL_TOOLS]
    assert "list_open_prs" in all_names
