"""Entry point: python -m crew ["task text"]

With a task argument, runs that one task and exits (local runs). With
no argument, runs the default self-build task first, then polls GitHub
issues labelled `crew-task` for the next task, sleeping POLL_SECONDS
between polls, until the credit/deadline stop rule fires. The loop
never re-runs a task the ledger or a "[crew] Done:" issue says is
finished, failed, or stalled — a stalled task hit the review gate
or produced nothing shippable and is not retried automatically;
open a new issue to re-queue it.

Loads config from env, validates it, builds the sequential workflow, runs
one task, records the run in the ledger, and posts a "done" issue update.
A failed feedback post warns on stderr but does not fail the run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

from .config import CrewConfig
from .credit import stop_reason
from .feedback import list_done_tasks, list_task_issues, report_done, report_stalled
from .blob_ledger import build_ledger
from .ledger import Ledger
from .orchestration import build_workflow, run_task
from .spend import TRACKER, reset_spend, restore_spend, spent_usd

# The crew's first task, agreed with the repo owner: build v1 of itself.
DEFAULT_TASK = (
    "Build v1 of the crew system itself in this repo: improve the crew "
    "package, tools, gates, and docs, opening a PR for each change."
)

# Poll interval in seconds (John, Nov 19, 2026: 1 minute). Overridable
# via CREW_POLL_SECONDS for local runs; the interval costs nothing
# extra — the replica runs either way, and a poll is one GitHub API call.
POLL_SECONDS = 60


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Quiet the chatty Azure loggers; the root handler captures everything.
    logging.getLogger("azure").setLevel(logging.WARNING)
    # httpx back at INFO (Nov 19): one line per model request is the
    # only sign of life a running task leaves in the logs — the crew
    # went silent for hours and nobody could tell if it was working.
    # Noisy by design; the framework warnings stay at WARNING on purpose.
    logging.getLogger("httpx").setLevel(logging.INFO)


def _settled_task_texts(ledger: Ledger) -> set[str]:
    """Task strings the ledger says are finished, failed, or stalled.

    Stalled counts as settled for the auto-loop: re-running a stalled
    task immediately would just burn another run on the same stall. A
    human re-queues it with a refined issue instead.
    """
    return {
        str(e.get("task", ""))
        for e in ledger.entries()
        if e.get("event") in ("task_finished", "task_failed", "task_stalled")
        and e.get("task")
    }


def _sign_offs_since(config: CrewConfig, before: int) -> list[dict] | None:
    """Reviewer sign-offs recorded since `before` (a count snapshot).

    Reads the local sign-off ledger (config.ledger_path) — the same
    file record_sign_off writes to. That is deliberately NOT the loop
    ledger: in production the loop ledger is the blob, while sign-offs
    land in the local file. Returns None when the ledger cannot be
    read at all, so the caller fails open with a warning instead of
    stalling every run on a ledger failure. (Known divergence: the
    sign-off record and the loop ledger are two files; unifying them
    is a separate change.)
    """
    try:
        return Ledger(config.ledger_path).sign_offs()[before:]
    except Exception as exc:  # noqa: BLE001 - ledger must not kill a run
        logging.getLogger("crew").warning(
            "could not read sign-off ledger: %s", exc
        )
        return None


def next_task(config: CrewConfig, ledger: Ledger) -> str | None:
    """Pick the next task, or None when there is nothing to do.

    First run: the default self-build task, unless it is already done.
    After that: the oldest open `crew-task` issue whose task is not
    finished, failed, stalled, or already covered by a "[crew] Done:"
    issue. A GitHub failure warns and returns None (sleep and retry)
    instead of crashing the loop.
    """
    log = logging.getLogger("crew")
    done = _settled_task_texts(ledger)
    try:
        done |= list_done_tasks(config.repo)
    except Exception as exc:  # noqa: BLE001 - a poll failure is not fatal
        log.warning("could not list done issues: %s", exc)
    if DEFAULT_TASK not in done:
        return DEFAULT_TASK
    try:
        issues = list_task_issues(config.repo)
    except Exception as exc:  # noqa: BLE001 - a poll failure is not fatal
        log.warning("could not list task issues: %s", exc)
        return None
    for issue in issues:
        if issue["task"] not in done:
            return issue["task"]
    return None


def _record(ledger: Ledger, event: str, **fields: object) -> None:
    """Best-effort ledger write: a ledger failure warns, never kills a run.

    The ledger is an audit trail, not the work itself. (A PermissionError
    here once crash-looped the whole container before any model call.)
    """
    try:
        ledger.record(event, **fields)
    except Exception as exc:  # noqa: BLE001 - ledger must not kill a run
        logging.getLogger("crew").warning("ledger %s failed: %s", event, exc)


def _record_spend(ledger: Ledger) -> None:
    """Best-effort `spend` event: the cumulative total at a boundary.

    The tracker restores from the last `spend` event after a restart,
    so these events — every $5 crossed plus each task boundary — are
    what keeps the total honest across restarts.
    """
    _record(
        ledger,
        "spend",
        usd=round(spent_usd(), 4),
        by_model=TRACKER.snapshot()["by_model"],
    )


def run_one_task(config: CrewConfig, ledger: Ledger, task: str) -> int:
    """Run a single task through the crew. Returns the exit code.

    The reviewer is the gate: after the workflow finishes, the run
    settles as stalled — not finished — when the reviewer requested
    changes, or when no review was recorded at all (no PR opened, or
    the reviewer never ran, so nothing shipped). A run that records
    task_finished with zero PRs is the Nov 19 hollow-done mistake;
    the gate exists to stop it repeating. An approved sign-off is
    what lets a run finish.
    """
    log = logging.getLogger("crew")
    _record(ledger, "task_started", task=task, repo=config.repo)
    try:
        before = len(Ledger(config.ledger_path).sign_offs())
    except Exception as exc:  # noqa: BLE001 - ledger must not kill a run
        log.warning("could not read sign-off ledger: %s", exc)
        before = None
    try:
        workflow = build_workflow(config)
        outputs = asyncio.run(run_task(workflow, task, config))
    except Exception as exc:  # noqa: BLE001 - report the failure, then exit 1
        _record(ledger, "task_failed", task=task, error=str(exc))
        log.error("task failed: %s", exc)
        return 1

    summary = "\n\n".join(str(o) for o in outputs) or "(no output)"
    if before is not None:
        sign_offs = _sign_offs_since(config, before)
        if sign_offs is not None:
            requested = [
                s for s in sign_offs if s.get("verdict") == "changes_requested"
            ]
            if requested:
                notes = "\n".join(
                    f"PR #{s.get('pr_number')}: {s.get('notes', '')}"
                    for s in requested
                )
                _record(ledger, "task_stalled", task=task, reason="review")
                log.warning(
                    "task stalled (reviewer requested changes):\n%s", notes
                )
                try:
                    url = report_stalled(
                        config.repo,
                        task,
                        f"Reviewer requested changes:\n{notes}\n\n{summary}",
                        reason="review",
                    )
                    log.info("stalled issue posted: %s", url)
                except Exception as exc:  # noqa: BLE001 - feedback must not fail the run
                    print(f"warning: could not post stalled issue: {exc}", file=sys.stderr)
                return 1
            if not sign_offs:
                _record(ledger, "task_stalled", task=task, reason="no_review")
                log.warning("task stalled (no review recorded):\n%s", summary)
                try:
                    url = report_stalled(config.repo, task, summary, reason="no_review")
                    log.info("stalled issue posted: %s", url)
                except Exception as exc:  # noqa: BLE001 - feedback must not fail the run
                    print(f"warning: could not post stalled issue: {exc}", file=sys.stderr)
                return 1
    _record(ledger, "task_finished", task=task, outputs=outputs)
    log.info("task finished:\n%s", summary)
    try:
        url = report_done(config.repo, task, summary)
        log.info("done issue posted: %s", url)
    except Exception as exc:  # noqa: BLE001 - feedback must not fail the run
        print(f"warning: could not post done issue: {exc}", file=sys.stderr)
    return 0


def poll_seconds() -> int:
    """Poll interval from the environment, default POLL_SECONDS."""
    try:
        return max(1, int(os.environ.get("CREW_POLL_SECONDS", str(POLL_SECONDS))))
    except ValueError:
        return POLL_SECONDS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crew", description="Run one crew task.")
    parser.add_argument(
        "task", nargs="?", default=None,
        help="the task for the crew, as one string (default: self-build, then poll crew-task issues)",
    )
    args = parser.parse_args(argv)
    setup_logging()
    log = logging.getLogger("crew")

    try:
        config = CrewConfig.from_env()
        config.validate()
    except ValueError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    ledger = build_ledger(config)

    # Spend tracking starts here: seed from the manual offset (spend
    # incurred before this guard existed), restore the total from the
    # last `spend` ledger event, and write a `spend` event at every
    # $5 crossed so a restart loses at most $5 of history.
    reset_spend(config.budget_spent_usd, config.budget_limit_usd)
    TRACKER.on_milestone = lambda usd, snap: _record(
        ledger, "spend", usd=usd, by_model=snap["by_model"]
    )
    restore_spend(ledger.entries())
    log.info("tracked spend at startup: $%.2f", spent_usd())

    # One-shot mode: an explicit task runs once and exits.
    if args.task:
        code = run_one_task(config, ledger, args.task)
        _record_spend(ledger)
        return code

    # Loop mode: default task first, then the issue queue, until the
    # credit/deadline stop rule fires.
    interval = poll_seconds()
    while True:
        reason = stop_reason(
            spent_usd(), config.budget_limit_usd, config.deadline_iso
        )
        if reason:
            log.info("stopping: %s", reason)
            return 0
        task = next_task(config, ledger)
        if task is None:
            log.info("no task queued, sleeping %ss", interval)
            time.sleep(interval)
            continue
        log.info("picked up task: %s", task[:120])
        run_one_task(config, ledger, task)
        _record_spend(ledger)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
