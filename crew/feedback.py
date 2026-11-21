"""Feedback channel: GitHub issues in the crew repo.

The crew reports progress and asks questions via issues and never blocks
waiting for an answer. When a task finishes, `report_done` posts a
"done" issue update.
"""

from __future__ import annotations

import json
import logging
import subprocess

from .config import ALLOWED_REPO
from .tools import _check_repo, _run

# Label an issue carries when it is a task for the crew to pick up.
TASK_LABEL = "crew-task"

# Label a stalled task issue carries, so its status shows on the
# original issue itself instead of only in a separate "[crew]
# Stalled:" issue. GitHub's red.
STALLED_LABEL = "stalled"
STALLED_LABEL_DESCRIPTION = "Task stalled; see the crew's comment for the reason"
STALLED_LABEL_COLOR = "d73a4a"

# Prefix of the title of a "done" issue posted by report_done().
DONE_TITLE_PREFIX = "[crew] Done: "

# Prefix of the title of a "stalled" issue posted by report_stalled().
# A stalled task is NOT done: the run hit the round/reset cap, so the
# issue asks for a refined task instead of claiming completion.
STALLED_TITLE_PREFIX = "[crew] Stalled: "


# GitHub caps issue titles at 256 characters. The task string is the
# full issue title plus body, so a stalled/done title built from it
# blows past the cap and `gh issue create` fails with a GraphQL
# error ("Title is too long"). Truncate the title to the cap; the
# body still carries the full task text.
GITHUB_TITLE_MAX_LENGTH = 256


def _issue_title(prefix: str, task: str) -> str:
    """An issue title that fits GitHub's 256-character cap."""
    return f"{prefix}{task}"[:GITHUB_TITLE_MAX_LENGTH]


def task_text(title: str, body: str = "") -> str:
    """The task string for an issue: title, plus body when there is one.

    This exact string is what the crew runs and what lands in the ledger,
    so the loop can tell a queued task from one already finished.
    """
    title = title.strip()
    body = (body or "").strip()
    return f"{title}\n\n{body}" if body else title


def _gh_json(repo: str, args: list[str]) -> list[dict]:
    """Run `gh` with `--json` and parse its stdout as a JSON list.

    Raises RuntimeError on a non-zero exit or unparsable output. Uses
    subprocess directly (not tools._run) because issue bodies can exceed
    _run's output cap and truncate the JSON.
    """
    proc = subprocess.run(
        ["gh", *args, "--repo", repo],
        capture_output=True, text=True, timeout=60,
    )
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args[:2])} failed (exit {proc.returncode}): "
            f"{out[:500]}"
        )
    try:
        parsed = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"could not parse gh output: {exc}")
    if not isinstance(parsed, list):
        raise RuntimeError("gh output was not a JSON list")
    return parsed


def _gh(repo: str, args: list[str]) -> str:
    """Run `gh` and return its stdout, stripped.

    Raises RuntimeError on a non-zero exit. Direct subprocess (not
    tools._run): comment bodies carry the full run summary and can
    exceed _run's output cap.
    """
    proc = subprocess.run(
        ["gh", *args, "--repo", repo],
        capture_output=True, text=True, timeout=60,
    )
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args[:2])} failed (exit {proc.returncode}): "
            f"{out[:500]}"
        )
    return proc.stdout.strip()


def comment_on_task_issue(repo: str, issue_number: int, body: str) -> str:
    """Comment on a task issue. Returns the comment URL.

    Direct subprocess, not tools._run: the body is the full run
    summary and can exceed _run's output cap.
    """
    _check_repo(repo)
    return _gh(
        repo,
        ["issue", "comment", str(issue_number), "--body", body],
    )


def ensure_label(repo: str, label: str, description: str, color: str) -> None:
    """Create a label, or update it when it already exists (`--force`).

    Raises on a gh failure; the caller decides whether that is fatal.
    """
    _check_repo(repo)
    _gh(
        repo,
        ["label", "create", label, "--description", description,
         "--color", color, "--force"],
    )


def tag_task_stalled(repo: str, issue_number: int) -> None:
    """Tag a task issue `stalled`. Best-effort: never raises.

    Settlement must never fail because labeling did, and the comment
    on the issue is the important part — a labeling failure warns
    and the run goes on without the tag.
    """
    log = logging.getLogger("crew.feedback")
    try:
        ensure_label(
            repo, STALLED_LABEL, STALLED_LABEL_DESCRIPTION, STALLED_LABEL_COLOR
        )
    except Exception as exc:  # noqa: BLE001 - labeling is best-effort
        log.warning("could not ensure stalled label: %s", exc)
        return
    try:
        _gh(repo, ["issue", "edit", str(issue_number),
                   "--add-label", STALLED_LABEL])
    except Exception as exc:  # noqa: BLE001 - labeling is best-effort
        log.warning("could not tag issue #%s stalled: %s", issue_number, exc)


def list_task_issues(repo: str) -> list[dict]:
    """Open issues labelled `crew-task`, oldest first.

    Each entry: {"number": int, "title": str, "body": str, "task": str}.
    `gh issue list` returns newest first, so the list is reversed.
    """
    _check_repo(repo)
    rows = _gh_json(
        repo,
        ["issue", "list", "--label", TASK_LABEL, "--state", "open",
         # 100, not 20: the list is newest-first and reversed to
         # oldest-first, so a 20-cap would hide the oldest tasks
         # once the queue passed 20 — exactly the tasks the loop
         # should run first.
         "--limit", "100", "--json", "number,title,body"],
    )
    out = [
        {
            "number": row.get("number"),
            "title": row.get("title", ""),
            "body": row.get("body", ""),
            "task": task_text(row.get("title", ""), row.get("body", "")),
        }
        for row in rows
    ]
    out.reverse()
    return out


def list_done_tasks(repo: str) -> set[str]:
    """Task strings that already have an open "[crew] Done:" issue.

    The ledger lives in the container's filesystem and does not survive
    a replica restart, so the done issues on GitHub are the durable
    record of what is finished. The caller strips the title prefix to
    recover the task string.
    """
    _check_repo(repo)
    rows = _gh_json(
        repo,
        ["issue", "list", "--state", "open", "--search", DONE_TITLE_PREFIX,
         "--limit", "50", "--json", "title"],
    )
    done = set()
    for row in rows:
        title = row.get("title", "")
        if title.startswith(DONE_TITLE_PREFIX):
            done.add(title[len(DONE_TITLE_PREFIX):])
    return done


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


def done_issue_payload(task: str, summary: str, pr_url: str = "") -> dict:
    """Pure payload for a "done" issue: title, body, labels. No gh call."""
    if not task:
        raise ValueError("task must not be empty")
    if not summary:
        raise ValueError("summary must not be empty")
    lines = [f"Task finished: {task}", "", summary]
    if pr_url:
        lines += ["", f"PR: {pr_url}"]
    return {
        "title": _issue_title(DONE_TITLE_PREFIX, task),
        "body": "\n".join(lines),
        "labels": ["done"],
    }


def report_done(repo: str, task: str, summary: str, pr_url: str = "") -> str:
    """Post a "done" issue update when a task finishes."""
    payload = done_issue_payload(task, summary, pr_url)
    return open_issue(repo, payload["title"], payload["body"])


def stalled_issue_payload(task: str, summary: str, reason: str = "cap") -> dict:
    """Pure payload for a "stalled" issue: title, body, labels. No gh call.

    reason names why the run stalled: "cap" (the old Magentic
    round/reset cap — kept for callers that still pass it), "review"
    (the reviewer requested changes; the summary carries the review
    notes), or "no_review" (no PR was opened and no review recorded,
    so nothing shipped). The body always says the task is not done.
    """
    if not task:
        raise ValueError("task must not be empty")
    if not summary:
        raise ValueError("summary must not be empty")
    if reason == "review":
        why = (
            "The reviewer requested changes, so this task is not done "
            "and is not retried automatically. Re-queue it with a new "
            "crew-task issue once the review notes below are addressed."
        )
    elif reason == "no_review":
        why = (
            "No PR was opened and no review was recorded, so nothing "
            "shipped and this task is not done. It is not retried "
            "automatically. Re-queue it with a refined crew-task issue."
        )
    else:
        why = (
            "The run hit the round/reset cap before finishing, so this task "
            "is not done and is not retried automatically. Re-queue it with "
            "a refined crew-task issue (narrower scope, or a higher "
            "CREW_MAX_ROUNDS)."
        )
    lines = [
        f"Task stalled: {task}",
        "",
        summary,
        "",
        why,
    ]
    return {
        "title": _issue_title(STALLED_TITLE_PREFIX, task),
        "body": "\n".join(lines),
        "labels": ["stalled"],
    }


def report_stalled(repo: str, task: str, summary: str, reason: str = "cap") -> str:
    """Post a "stalled" issue update when a task stalls (cap or review)."""
    payload = stalled_issue_payload(task, summary, reason=reason)
    return open_issue(repo, payload["title"], payload["body"])


__all__ = [
    "open_issue", "comment_issue", "comment_on_task_issue", "ensure_label",
    "tag_task_stalled", "ask_question", "report_done",
    "report_stalled", "done_issue_payload", "stalled_issue_payload",
    "task_text", "list_task_issues",
    "list_done_tasks", "TASK_LABEL", "STALLED_LABEL",
    "STALLED_LABEL_DESCRIPTION", "STALLED_LABEL_COLOR",
    "DONE_TITLE_PREFIX", "STALLED_TITLE_PREFIX",
]
