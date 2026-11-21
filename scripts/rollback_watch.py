#!/usr/bin/env python3
"""Watch a new Container Apps revision after deploy; roll it back if unhealthy.

A bad crew change that passes tests ships via auto-deploy, and the loop
is the thing that broke — so it cannot repair itself. This is the
safety net: after the deploy job verifies the new revision is ready,
it watches the new revision and, if the revision crash-loops or goes
silent, reactivates the previous (last good) revision and deactivates
the new one, then fails the workflow loudly so the rollback is visible.

Health needs BOTH signals, unless nothing is queued:
- a replica of the new revision is Running (a crash-looping revision
  never holds a running replica), and
- a ledger heartbeat: a task_started, task_finished, or spend event
  in the blob ledger timestamped at or after the new revision was
  created. The loop writes task_started at the start of every run, so
  a live loop writes within ~2 minutes of replica start.
- When no task is queued (the default task is settled and no open
  crew-task issue has an unsettled task string), a running replica
  with no heartbeat is healthy, not silent — an idle loop is not a
  broken one. Silence only rolls back when work is queued but the
  loop is not doing it.

Exit codes: 0 healthy (deploy stands), 1 unhealthy and rolled back,
2 cannot verify (no previous revision to roll back to, or the ledger
could not be read) — no rollback happens on 2, the failure is loud
instead of guessing.

Prerequisite: whoever runs this needs Storage Blob Data Reader on the
ledger storage account to read the heartbeat. The deploy service
principal does NOT have it by default — grant it once (the bicep
takes an optional deployServicePrincipalId for this), e.g.:
  az role assignment create --role "Storage Blob Data Reader" \
    --assignee-object-id <deploy-SP-object-id> \
    --assignee-principal-type ServicePrincipal \
    --scope <ledger-storage-account-resource-id>
Until then the watch fails loudly (exit 2) instead of rolling back.

Limitations (shortcut: one level of rollback): if the previous
revision is also bad, this does not help — a human still has to
intervene.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HEARTBEAT_EVENTS = frozenset({"task_started", "task_finished", "spend"})
SETTLED_EVENTS = frozenset({"task_finished", "task_failed", "task_stalled"})

EXIT_HEALTHY = 0
EXIT_ROLLED_BACK = 1
EXIT_CANNOT_VERIFY = 2

DEFAULT_RESOURCE_GROUP = "rg-agent-crew"
DEFAULT_APP = "ca-agent-crew"
DEFAULT_REPO = "warni001/agent-crew"
DEFAULT_WATCH_SECONDS = 600  # 10 minutes: container startup plus a first task
DEFAULT_POLL_SECONDS = 30

TASK_LABEL = "crew-task"
DONE_TITLE_PREFIX = "[crew] Done: "
DEFAULT_TASK = (
    "Build v1 of the crew system itself in this repo: improve the crew "
    "package, tools, gates, and docs, opening a PR for each change."
)


def parse_ts(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp to an aware datetime; None if unparseable."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def has_heartbeat(entries: list[dict], since: datetime) -> bool:
    """True if any heartbeat event is timestamped at or after `since`."""
    for entry in entries:
        if entry.get("event") not in HEARTBEAT_EVENTS:
            continue
        ts = parse_ts(entry.get("ts"))
        if ts is not None and ts >= since:
            return True
    return False


def replica_running(states: list[str]) -> bool:
    """True if at least one replica of the revision is Running."""
    return "Running" in states


def task_text(title: str, body: str = "") -> str:
    """The task string for an issue: title, plus body when there is one.

    This exact string is what the crew runs and what lands in the
    ledger, so the loop can tell a queued task from one already
    settled. Must match crew/feedback.task_text.
    """
    title = title.strip()
    body = (body or "").strip()
    return f"{title}\n\n{body}" if body else title


def settled_task_texts(entries: list[dict]) -> set[str]:
    """Task strings the ledger says are finished, failed, or stalled."""
    return {
        str(e.get("task", ""))
        for e in entries
        if e.get("event") in SETTLED_EVENTS and e.get("task")
    }


def list_open_task_issues(runner, repo: str) -> list[dict]:
    """Open issues labelled crew-task, with number/title/body."""
    out = runner([
        "gh", "issue", "list", "--repo", repo,
        "--label", TASK_LABEL, "--state", "open",
        "--limit", "100", "--json", "number,title,body",
    ])
    return json.loads(out)


def list_done_task_texts(runner, repo: str) -> set[str]:
    """Task strings that already have an open "[crew] Done:" issue."""
    out = runner([
        "gh", "issue", "list", "--repo", repo, "--state", "open",
        "--search", DONE_TITLE_PREFIX, "--limit", "50", "--json", "title",
    ])
    done = set()
    for row in json.loads(out):
        title = row.get("title", "")
        if title.startswith(DONE_TITLE_PREFIX):
            done.add(title[len(DONE_TITLE_PREFIX):])
    return done


def has_queued_task(runner, repo: str, entries: list[dict]) -> bool:
    """True if the crew has a task to pick up.

    Mirrors the crew loop's selection: the default task if it is not
    settled, else the oldest open crew-task issue whose task string
    is not settled in the ledger or covered by a Done issue. No
    queued task means an idle loop is healthy, not silent.
    """
    done = settled_task_texts(entries) | list_done_task_texts(runner, repo)
    if DEFAULT_TASK not in done:
        return True
    for issue in list_open_task_issues(runner, repo):
        if task_text(issue.get("title", ""), issue.get("body", "")) not in done:
            return True
    return False


def decide(running: bool, heartbeat: bool, queued: bool = True) -> str:
    """Health verdict: 'healthy', 'crash-loop', or 'silent'.

    A running replica with no heartbeat is healthy when nothing is
    queued (idle loop), silent only when work is queued but the loop
    is not doing it. `queued` defaults to True so a caller that does
    not know keeps the old strict behaviour.
    """
    if running and heartbeat:
        return "healthy"
    if not running:
        return "crash-loop"
    if not queued:
        return "healthy"
    return "silent"


def _run(cmd: list[str]) -> str:
    """Run a command (no shell), return stdout, raise on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc.stdout


def revision_created_time(runner, resource_group: str, app: str, revision: str) -> datetime:
    """Creation time of a revision, from `az containerapp revision show`."""
    out = runner(
        [
            "az", "containerapp", "revision", "show",
            "-g", resource_group, "-n", app, "--revision", revision,
            "--query", "properties.createdTime", "-o", "tsv",
        ]
    ).strip()
    created = parse_ts(out)
    if created is None:
        raise RuntimeError(f"could not parse revision createdTime: {out!r}")
    return created


def replica_states(runner, resource_group: str, app: str, revision: str) -> list[str]:
    """Running states of a revision's replicas, from `az containerapp replica list`."""
    out = runner(
        [
            "az", "containerapp", "replica", "list",
            "-g", resource_group, "-n", app, "--revision", revision,
            "--query", "[].properties.runningState", "-o", "tsv",
        ]
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def ledger_blob_url(runner, resource_group: str, app: str) -> str:
    """The ledger blob URL from the app's own env (authoritative, no guessing)."""
    out = runner(
        [
            "az", "containerapp", "show",
            "-g", resource_group, "-n", app,
            "--query",
            "properties.template.containers[0].env[?name=='CREW_LEDGER_BLOB_URL'].value | [0]",
            "-o", "tsv",
        ]
    ).strip()
    if not out:
        raise RuntimeError("CREW_LEDGER_BLOB_URL is not set on the container app")
    return out


def ledger_entries(runner, blob_url: str) -> list[dict]:
    """All entries in the ledger blob, via `az storage blob download`."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "crew-ledger.jsonl"
        runner(
            [
                "az", "storage", "blob", "download",
                "--blob-url", blob_url, "--auth-mode", "login",
                "--file", str(path), "--overwrite", "-o", "none",
            ]
        )
        entries: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def rollback(runner, resource_group: str, app: str, previous: str, new: str) -> None:
    """Reactivate the last good revision, then deactivate the new one."""
    runner(
        ["az", "containerapp", "revision", "activate",
         "-g", resource_group, "-n", app, "--revision", previous]
    )
    runner(
        ["az", "containerapp", "revision", "deactivate",
         "-g", resource_group, "-n", app, "--revision", new]
    )


def watch(
    resource_group: str,
    app: str,
    new_revision: str,
    previous_revision: str,
    watch_seconds: int = DEFAULT_WATCH_SECONDS,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
    runner=_run,
    sleep=time.sleep,
    now=lambda: datetime.now(timezone.utc),
    repo: str = DEFAULT_REPO,
) -> int:
    """Watch the new revision; roll back on crash-loop or silence.

    Returns an exit code: 0 healthy, 1 rolled back, 2 cannot verify.
    A running replica with no heartbeat is healthy when nothing is
    queued — an idle loop is not a silent one.
    """
    if not previous_revision or previous_revision == new_revision:
        print(
            "cannot roll back: no usable previous revision "
            f"(previous={previous_revision!r}, new={new_revision!r})",
            file=sys.stderr,
        )
        return EXIT_CANNOT_VERIFY
    try:
        since = revision_created_time(runner, resource_group, app, new_revision)
        blob_url = ledger_blob_url(runner, resource_group, app)
    except RuntimeError as exc:
        print(f"cannot verify new revision: {exc}", file=sys.stderr)
        return EXIT_CANNOT_VERIFY

    deadline = now() + timedelta(seconds=watch_seconds)
    verdict = "silent"
    while True:
        try:
            running = replica_running(
                replica_states(runner, resource_group, app, new_revision)
            )
            entries = ledger_entries(runner, blob_url)
            heartbeat = has_heartbeat(entries, since)
            queued = has_queued_task(runner, repo, entries)
        except RuntimeError as exc:
            # A failed read is not a signal either way: fail loudly
            # without rolling back. Rolling back on an unverifiable
            # signal would revert healthy deploys (e.g. the deploy SP
            # has no Storage Blob Data Reader yet).
            print(f"cannot verify new revision: {exc}", file=sys.stderr)
            return EXIT_CANNOT_VERIFY
        verdict = decide(running, heartbeat, queued)
        if verdict == "healthy":
            if queued:
                print(f"revision {new_revision} is healthy (running replica + heartbeat).")
            else:
                print(f"revision {new_revision} is healthy (running replica, no task queued).")
            return EXIT_HEALTHY
        if now() >= deadline:
            break
        sleep(poll_seconds)

    print(f"revision {new_revision} is unhealthy: {verdict}. Rolling back to {previous_revision}.")
    try:
        rollback(runner, resource_group, app, previous_revision, new_revision)
    except RuntimeError as exc:
        print(f"rollback failed: {exc}", file=sys.stderr)
        return EXIT_ROLLED_BACK
    print(f"rolled back to {previous_revision}; new revision {new_revision} deactivated.")
    return EXIT_ROLLED_BACK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", default=DEFAULT_RESOURCE_GROUP)
    parser.add_argument("--app", default=DEFAULT_APP)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--new-revision", required=True)
    parser.add_argument("--previous-revision", required=True)
    parser.add_argument("--watch-seconds", type=int, default=DEFAULT_WATCH_SECONDS)
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    args = parser.parse_args(argv)
    return watch(
        args.resource_group,
        args.app,
        args.new_revision,
        args.previous_revision,
        watch_seconds=args.watch_seconds,
        poll_seconds=args.poll_seconds,
        repo=args.repo,
    )


if __name__ == "__main__":
    raise SystemExit(main())
