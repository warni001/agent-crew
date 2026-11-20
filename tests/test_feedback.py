"""Feedback "done" issue payload shape.

Contract under test (see docs/crew-instructions.md, "Module contract")::

    crew.feedback.done_issue_payload(task_id, summary, pr_url=None) -> dict

The crew never blocks waiting for a human. When a task finishes it
posts a "done" issue to GitHub: title names the task and says done,
body carries the summary (and PR link when there is one), labels
include ``"done"``. The payload shape is the contract — the posting
itself goes through the mocked ``gh`` CLI, never the live API.

Blocked until ``crew/feedback.py`` is merged; these tests skip until then.
"""

from __future__ import annotations

import pytest

from conftest import require_crew


@pytest.fixture
def feedback_mod():
    return require_crew("feedback")


def test_payload_has_required_keys(feedback_mod):
    payload = feedback_mod.done_issue_payload("crew-v1", "Scaffold merged.")
    assert set(payload) >= {"title", "body", "labels"}


def test_title_names_task_and_done(feedback_mod):
    payload = feedback_mod.done_issue_payload("crew-v1", "Scaffold merged.")
    assert "crew-v1" in payload["title"]
    assert "done" in payload["title"].lower()


def test_body_carries_summary(feedback_mod):
    payload = feedback_mod.done_issue_payload("crew-v1", "Scaffold merged, tests green.")
    assert "Scaffold merged, tests green." in payload["body"]


def test_labels_include_done(feedback_mod):
    payload = feedback_mod.done_issue_payload("crew-v1", "Done.")
    assert "done" in [str(label).lower() for label in payload["labels"]]


def test_pr_url_included_in_body_when_given(feedback_mod):
    payload = feedback_mod.done_issue_payload(
        "crew-v1", "Done.", pr_url="https://github.com/warni001/agent-crew/pull/1"
    )
    assert "https://github.com/warni001/agent-crew/pull/1" in payload["body"]


def test_pr_url_absent_when_not_given(feedback_mod):
    payload = feedback_mod.done_issue_payload("crew-v1", "Done.")
    assert "http" not in payload["body"]


def test_empty_summary_rejected(feedback_mod):
    with pytest.raises(ValueError):
        feedback_mod.done_issue_payload("crew-v1", "")


def test_empty_task_id_rejected(feedback_mod):
    with pytest.raises(ValueError):
        feedback_mod.done_issue_payload("", "Done.")


def test_stalled_payload_review_reason(feedback_mod):
    payload = feedback_mod.stalled_issue_payload(
        "Fix X", "Reviewer requested changes:\nPR #7: fix the test",
        reason="review",
    )
    assert payload["title"] == "[crew] Stalled: Fix X"
    assert "not done" in payload["body"]
    assert "requested changes" in payload["body"]
    assert "round/reset cap" not in payload["body"]


def test_stalled_payload_no_review_reason(feedback_mod):
    payload = feedback_mod.stalled_issue_payload(
        "Fix X", "merged nothing", reason="no_review"
    )
    assert "not done" in payload["body"]
    assert "No PR was opened" in payload["body"]
    assert "round/reset cap" not in payload["body"]


def test_stalled_payload_defaults_to_cap(feedback_mod):
    payload = feedback_mod.stalled_issue_payload("Fix X", "hit the cap")
    assert "round/reset cap" in payload["body"]
