"""Entry point: python -m crew ["task text"]

With a task argument, runs that one task and exits (local runs). With
no argument, runs the default self-build task first, then polls GitHub
issues labelled `crew-task` for the next task, sleeping POLL_SECONDS
between polls, until the credit/deadline stop rule fires. The loop
never re-runs a task the ledger or a "[crew] Done:" issue says is
finished or failed — a failed task is not retried automatically; open
a new issue to re-queue it.

Loads config from env, validates it, builds the Magentic workflow, runs
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
from .feedback import list_done_tasks, list_task_issues, report_done
from .ledger import Ledger
from .orchestration import build_workflow, run_task

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


def _done_task_texts(ledger: Ledger) -> set[str]:
    """Task strings the ledger says are finished or failed."""
    return {
        str(e.get("task", ""))
        for e in ledger.entries()
        if e.get("event") in ("task_finished", "task_failed")
        and e.get("task")
    }


def next_task(config: CrewConfig, ledger: Ledger) -> str | None:
    """Pick the next task, or None when there is nothing to do.

    First run: the default self-build task, unless it is already done.
    After that: the oldest open `crew-task` issue whose task is not
    finished, failed, or already covered by a "[crew] Done:" issue.
    A GitHub failure warns and returns None (sleep and retry) instead
    of crashing the loop.
    """
    log = logging.getLogger("crew")
    done = _done_task_texts(ledger)
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


def run_one_task(config: CrewConfig, ledger: Ledger, task: str) -> int:
    """Run a single task through the crew. Returns the exit code."""
    log = logging.getLogger("crew")
    _record(ledger, "task_started", task=task, repo=config.repo)
    try:
        workflow = build_workflow(config)
        outputs = asyncio.run(run_task(workflow, task, config))
    except Exception as exc:  # noqa: BLE001 - report the failure, then exit 1
        _record(ledger, "task_failed", task=task, error=str(exc))
        log.error("task failed: %s", exc)
        return 1

    summary = "\n\n".join(str(o) for o in outputs) or "(no output)"
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

    ledger = Ledger(config.ledger_path)

    # One-shot mode: an explicit task runs once and exits.
    if args.task:
        return run_one_task(config, ledger, args.task)

    # Loop mode: default task first, then the issue queue, until the
    # credit/deadline stop rule fires.
    interval = poll_seconds()
    while True:
        reason = stop_reason(
            config.budget_spent_usd, config.budget_limit_usd, config.deadline_iso
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
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
