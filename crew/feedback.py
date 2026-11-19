"""Feedback channel: GitHub issues in the crew repo.

The crew reports progress and asks questions via issues and never blocks
waiting for an answer. When a task finishes, `report_done` posts a
"done" issue update.
"""

from __future__ import annotations

from .config import ALLOWED_REPO
from .tools import _check_repo, _run


def open_issue(repo: str, title: str, body: str) -> str:
    """Open an issue. Returns the issue URL."""
    _check_repo(repo)
    return _run(
        ["gh", "issue", "create", "--repo", repo,
         "--title", title, "--body", body]
    )


def comment_issue(repo: str, issue_number: int, body: str) -> str:
    """Comment on an issue."""
    _check_repo(repo)
    return _run(
        ["gh", "issue", "comment", str(issue_number),
         "--repo", repo, "--body", body]
    )


def ask_question(repo: str, question: str, context: str = "") -> str:
    """Ask the repo owner a question via an issue. Fire-and-forget: the
    crew keeps working and never waits for a reply."""
    body = question if not context else f"{question}\n\nContext:\n{context}"
    return open_issue(repo, f"[crew] Question: {question[:80]}", body)


def report_done(repo: str, task: str, summary: str, pr_url: str = "") -> str:
    """Post a "done" issue update when a task finishes."""
    lines = [f"Task finished: {task}", "", summary]
    if pr_url:
        lines += ["", f"PR: {pr_url}"]
    return open_issue(repo, f"[crew] Done: {task[:80]}", "\n".join(lines))


__all__ = ["open_issue", "comment_issue", "ask_question", "report_done"]
