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

import json
from subprocess import CompletedProcess

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


def test_long_task_title_truncated_to_github_cap(feedback_mod):
    """A title built from a long task fits GitHub's 256-char cap;
    a short title is unchanged (see test_stalled_payload_review_reason)."""
    long_task = "Merge the approved open PRs\n\n" + "x" * 500
    payload = feedback_mod.stalled_issue_payload(long_task, "hit the cap")
    assert len(payload["title"]) == 256
    assert payload["title"].startswith("[crew] Stalled: Merge the approved")
    done = feedback_mod.done_issue_payload(long_task, "finished")
    assert len(done["title"]) == 256
    assert done["title"].startswith("[crew] Done: Merge the approved")


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


# --- stall marking on the original issue (comment + stalled label) ---


def _ok_proc(monkeypatch, feedback_mod, calls):
    """Mock subprocess.run: record argv, succeed with empty output."""
    from unittest import mock

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(feedback_mod.subprocess, "run", fake_run)


def test_comment_on_task_issue_posts_via_gh(feedback_mod, monkeypatch):
    calls = []
    _ok_proc(monkeypatch, feedback_mod, calls)
    url = feedback_mod.comment_on_task_issue(
        "warni001/agent-crew", 42, "Stalled: no_review\n\nnothing shipped"
    )
    assert url == ""
    assert calls == [
        ["gh", "issue", "comment", "42", "--body",
         "Stalled: no_review\n\nnothing shipped",
         "--repo", "warni001/agent-crew"]
    ]


def test_comment_on_task_issue_rejects_other_repos(feedback_mod):
    with pytest.raises(ValueError):
        feedback_mod.comment_on_task_issue("someone-else/other-repo", 1, "x")


def test_close_task_issue_posts_comment_via_gh(feedback_mod, monkeypatch):
    calls = []
    _ok_proc(monkeypatch, feedback_mod, calls)
    feedback_mod.close_task_issue(
        "warni001/agent-crew", 42, "Stalled: no_review\n\nnothing shipped"
    )
    assert calls == [
        ["gh", "issue", "close", "42", "--comment",
         "Stalled: no_review\n\nnothing shipped", "--repo", "warni001/agent-crew"]
    ]


@pytest.mark.parametrize("repo, number", [
    ("someone-else/other-repo", 42),
    ("warni001/agent-crew", "https://github.com/someone-else/other-repo/issues/42"),
    ("warni001/agent-crew", 0),
])
def test_close_task_issue_rejects_out_of_scope_target(feedback_mod, monkeypatch, repo, number):
    calls = []
    _ok_proc(monkeypatch, feedback_mod, calls)
    with pytest.raises(ValueError):
        feedback_mod.close_task_issue(repo, number, "Done")
    assert not calls


def test_ensure_label_creates_with_force(feedback_mod, monkeypatch):
    calls = []
    _ok_proc(monkeypatch, feedback_mod, calls)
    feedback_mod.ensure_label(
        "warni001/agent-crew", "stalled", "Task stalled", "d73a4a"
    )
    assert calls == [
        ["gh", "label", "create", "stalled", "--description", "Task stalled",
         "--color", "d73a4a", "--force", "--repo", "warni001/agent-crew"]
    ]


def test_ensure_label_rejects_other_repos(feedback_mod):
    with pytest.raises(ValueError):
        feedback_mod.ensure_label("someone-else/other-repo", "x", "y", "z")


def test_tag_task_stalled_ensures_label_then_adds_it(feedback_mod, monkeypatch):
    calls = []
    _ok_proc(monkeypatch, feedback_mod, calls)
    feedback_mod.tag_task_stalled("warni001/agent-crew", 42)
    assert calls == [
        ["gh", "label", "create", "stalled", "--description",
         feedback_mod.STALLED_LABEL_DESCRIPTION,
         "--color", "d73a4a", "--force", "--repo", "warni001/agent-crew"],
        ["gh", "issue", "edit", "42", "--add-label", "stalled",
         "--repo", "warni001/agent-crew"],
    ]


def test_tag_task_stalled_tolerates_label_failure(feedback_mod, monkeypatch):
    from unittest import mock

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "label" in argv:
            return mock.Mock(returncode=1, stdout="", stderr="boom")
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(feedback_mod.subprocess, "run", fake_run)
    # No raise: a labeling failure must never fail settlement.
    feedback_mod.tag_task_stalled("warni001/agent-crew", 42)
    assert len(calls) == 1  # label create only; the edit is skipped


def test_tag_task_stalled_tolerates_edit_failure(feedback_mod, monkeypatch):
    from unittest import mock

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "edit" in argv:
            return mock.Mock(returncode=1, stdout="", stderr="boom")
        return mock.Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(feedback_mod.subprocess, "run", fake_run)
    feedback_mod.tag_task_stalled("warni001/agent-crew", 42)
    assert len(calls) == 2  # label create + edit attempted, no raise


@pytest.fixture
def report_issues(feedback_mod, monkeypatch):
    """Keep issue state across mocked GitHub commands."""
    issues = []

    def run(argv, **kwargs):
        if argv[1] == "api":
            assert "--paginate" in argv and "--slurp" in argv
            assert argv[-1] == "repos/warni001/agent-crew/issues?state=open&per_page=100"
            output = json.dumps([[row] for row in issues if row["state"] == "open"])
        elif argv[1:3] == ["issue", "create"]:
            number = len(issues) + 1
            output = f"https://github.com/warni001/agent-crew/issues/{number}"
            issues.append({
                "number": number, "html_url": output, "labels": [], "state": "open",
                "title": argv[argv.index("--title") + 1],
                "body": argv[argv.index("--body") + 1], "comments": [],
            })
        else:
            assert argv[1:3] in (["issue", "comment"], ["issue", "close"])
            row = issues[int(argv[3]) - 1]
            row["comments"].append(argv[argv.index(
                "--comment" if argv[2] == "close" else "--body"
            ) + 1])
            if argv[2] == "close":
                assert any(
                    other is not row and other["state"] == "open"
                    and other["html_url"] in row["comments"][-1]
                    for other in issues
                )
                row["state"] = "closed"
            output = row["html_url"]
        return CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(feedback_mod.subprocess, "run", run)
    return issues


@pytest.mark.parametrize("outcome", ["done", "stalled"])
@pytest.mark.parametrize("issue_number", [None, 84])
def test_repeated_settlement_comments(feedback_mod, report_issues, outcome, issue_number):
    report = getattr(feedback_mod, f"report_{outcome}")
    repo = "warni001/agent-crew"
    task = "Fix X\n\n" + "x" * 300
    first_url = report(repo, task, "First summary", issue_number=issue_number)
    later_task = task if issue_number is None else "Edited task title and body"
    assert report(repo, later_task, "Second summary", issue_number=issue_number) == first_url
    [issue] = report_issues
    assert issue["state"] == "open"
    [comment] = issue["comments"]
    assert "Second summary" in comment


def test_done_supersedes_legacy_stall(feedback_mod, report_issues):
    repo = "warni001/agent-crew"
    task = "Fix X\n\n" + "x" * 300
    other = feedback_mod.stalled_issue_payload(task + "different", "Unrelated stall")
    legacy = feedback_mod.stalled_issue_payload(task, "Previous stall")
    assert other["title"] == legacy["title"]
    feedback_mod.open_issue(repo, other["title"], other["body"])
    feedback_mod.open_issue(repo, legacy["title"], legacy["body"])

    done_url = feedback_mod.report_done(repo, task, "Finished", issue_number=84)

    unrelated, stalled, done = report_issues
    assert unrelated["state"] == "open"
    assert stalled["state"] == "closed"
    assert stalled["comments"] == [f"Superseded by {done_url}"]
    assert done["state"] == "open"
    assert done["title"].startswith(feedback_mod.DONE_TITLE_PREFIX)


def test_lookup_failure_does_not_create_report(feedback_mod, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[1])
        return CompletedProcess(argv, 1, "", "lookup failed")

    monkeypatch.setattr(feedback_mod.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="lookup failed"):
        feedback_mod.report_done("warni001/agent-crew", "Fix X", "Finished")
    assert calls == ["api"]
