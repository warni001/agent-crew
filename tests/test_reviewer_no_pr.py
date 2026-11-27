"""The reviewer must never guess a PR number.

Regression (Nov 27, 2026): the #75 run stalled with no_review
because the builder produced no PR and the reviewer — whose only
context was the builder's refusal — invented PR #1, which does not
exist, and wrote 'changes requested' as text without ever calling
record_sign_off. Its tools all need a PR number, and its
instructions gave it no 'no PR' path. The fix: the reviewer finds
the PR from list_open_prs, never guesses a number, and records
nothing when there is no PR to review.

Stopping rule (Nov 27, 2026): the reviewer kept finding a new
blocker on every round of the caps work — #75, then #89, each one
further out on the tail of edge cases, at roughly $3 and 30 minutes
a round — because its instructions gave it no sense of proportion
and no stopping rule. The fix: it weighs each objection against
another round, and before requesting changes it checks the sign-off
ledger — a prior changes_requested means the PR has already been
around the loop once, so it approves with the residual risks noted
instead of requesting changes again.
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


def test_reviewer_weighs_objections_against_another_round():
    roles_mod = require_crew("roles")
    text = roles_mod._REVIEWER_INSTRUCTIONS
    assert "roughly $3" in text
    assert "30 minutes" in text
    assert "not a reason to block" in text
    assert "check_review_sign_off" in text
    assert "already been around the loop once" in text
    assert "thinner tail of edge cases" in text


def test_check_review_sign_off_is_in_reviewer_toolset():
    """The reviewer needs check_review_sign_off to tell a first review
    from a rework round: a prior changes_requested sign-off means this
    PR has already been around the loop once, so the reviewer escalates
    instead of requesting changes again (the #89 round proved the loop
    — the reviewer found a new blocker on every round, each one further
    out on the tail of edge cases)."""
    tools_mod = require_crew("tools")
    names = [t.name for t in tools_mod.REVIEW_TOOLS]
    assert "check_review_sign_off" in names
    all_names = [t.name for t in tools_mod.ALL_TOOLS]
    assert "check_review_sign_off" in all_names
