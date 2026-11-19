"""Feedback channel: GitHub issues in the crew repo.

The crew reports progress and asks questions via issues and never blocks
waiting for an answer. When a task finishes, `report_done` posts a
"done" issue update.
"""

from __future__ import annotations

import json
import subprocess

from .config import ALLOWED_REPO
from .tools import _check_repo, _run

# Label an issue carries when it is a task for the crew to pick up.
TASK_LABEL = "crew-task"

# Prefix of the title of a "done" issue posted by report_done().
DONE_TITLE_PREFIX = "[crew] Done: "

# Prefix of the title of a "stalled" issue posted by report_stalled().
# A stalled task is NOT done: the run hit the round/reset cap, so the
# issue asks for a refined task instead of claiming completion.
STALLED_TITLE_PREFIX = "[crew] Stalled: "


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


def list_task_issues(repo: str) -> list[dict]:
    """Open issues labelled `crew-task`, oldest first.

    Each entry: {"number": int, "title": str, "body": str, "task": str}.
    `gh issue list` returns newest first, so the list is reversed.
    """
    _check_repo(repo)
    rows = _gh_json(
        repo,
        ["issue", "list", "--label", TASK_LABEL, "--state", "open",
         "--limit", "20", "--json", "number,title,body"],
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
        "title": f"[crew] Done: {task}",
        "body": "\n".join(lines),
        "labels": ["done"],
    }


def report_done(repo: str, task: str, summary: str, pr_url: str = "") -> str:
    """Post a "done" issue update when a task finishes."""
    payload = done_issue_payload(task, summary, pr_url)
    return open_issue(repo, payload["title"], payload["body"])


def stalled_issue_payload(task: str, summary: str) -> dict:
    """Pure payload for a "stalled" issue: title, body, labels. No gh call."""
    if not task:
        raise ValueError("task must not be empty")
    if not summary:
        raise ValueError("summary must not be empty")
    lines = [
        f"Task stalled: {task}",
        "",
        summary,
        "",
        "The run hit the round/reset cap before finishing, so this task "
        "is not done and is not retried automatically. Re-queue it with "
        "a refined crew-task issue (narrower scope, or a higher "
        "CREW_MAX_ROUNDS).",
    ]
    return {
        "title": f"[crew] Stalled: {task}",
        "body": "\n".join(lines),
        "labels": ["stalled"],
    }


def report_stalled(repo: str, task: str, summary: str) -> str:
    """Post a "stalled" issue update when a task hits the round cap."""
    payload = stalled_issue_payload(task, summary)
    return open_issue(repo, payload["title"], payload["body"])


__all__ = [
    "open_issue", "comment_issue", "ask_question", "report_done",
    "report_stalled", "done_issue_payload", "stalled_issue_payload",
    "task_text", "list_task_issues",
    "list_done_tasks", "TASK_LABEL", "DONE_TITLE_PREFIX",
    "STALLED_TITLE_PREFIX",
]
