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
from .feedback import (
    close_task_issue,
    list_done_tasks,
    list_task_issues,
    report_done,
    report_stalled,
    tag_task_stalled,
)
from .blob_ledger import build_ledger
from .ledger import Ledger
from .orchestration import builder_tool_calls, format_run_summary, run_task
from .roles import build_roles
from .spend import (
    TRACKER,
    TaskLimitExceeded,
    TaskTimeLimitExceeded,
    check_task_cost,
    remaining_task_seconds,
    reset_spend,
    restore_spend,
    spent_usd,
    task_limits,
)
from .tools import ensure_task_branch, task_branch_name

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
    read, so the caller can stall without claiming there was no review.
    Unifying the sign-off record and the loop ledger is a separate
    change.
    """
    try:
        return Ledger(config.ledger_path).sign_offs()[before:]
    except Exception as exc:  # noqa: BLE001 - ledger must not kill a run
        logging.getLogger("crew").warning(
            "could not read sign-off ledger: %s", exc
        )
        return None


def next_task_issue(config: CrewConfig, ledger: Ledger) -> dict | None:
    """Pick the next task, with its issue number — or None when there
    is nothing to do.

    Returns {"task": str, "number": int | None}: the number is the
    original `crew-task` issue, None for the default task, which has
    no issue. The loop needs the number to close the original issue
    when the task settles. Picking and skipping are exactly
    next_task's; this only also returns the number.
    """
    log = logging.getLogger("crew")
    done = _settled_task_texts(ledger)
    try:
        done |= list_done_tasks(config.repo)
    except Exception as exc:  # noqa: BLE001 - a poll failure is not fatal
        log.warning("could not list done issues: %s", exc)
    if DEFAULT_TASK not in done:
        return {"task": DEFAULT_TASK, "number": None}
    try:
        issues = list_task_issues(config.repo)
    except Exception as exc:  # noqa: BLE001 - a poll failure is not fatal
        log.warning("could not list task issues: %s", exc)
        return None
    for issue in issues:
        if issue["task"] not in done:
            return {"task": issue["task"], "number": issue["number"]}
    return None


def next_task(config: CrewConfig, ledger: Ledger) -> str | None:
    """Pick the next task, or None when there is nothing to do.

    First run: the default self-build task, unless it is already done.
    After that: the oldest open `crew-task` issue whose task is not
    finished, failed, stalled, or already covered by a "[crew] Done:"
    issue. A GitHub failure warns and returns None (sleep and retry)
    instead of crashing the loop.
    """
    picked = next_task_issue(config, ledger)
    return picked["task"] if picked else None


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


def _settle_task_issue(
    config: CrewConfig, issue_number: int | None, comment: str, *, stalled: bool = False
) -> None:
    """Close the original issue and, for stalls, tag it independently."""
    if issue_number is None:
        return
    log = logging.getLogger("crew")
    try:
        close_task_issue(config.repo, issue_number, comment)
        log.info("closed task issue #%s", issue_number)
    except Exception as exc:  # noqa: BLE001 - feedback must not fail the run
        log.warning("could not close task issue #%s: %s", issue_number, exc)
    if stalled:
        try:
            tag_task_stalled(config.repo, issue_number)
        except Exception as exc:  # noqa: BLE001 - feedback must not fail the run
            log.warning("could not tag task issue #%s: %s", issue_number, exc)


async def _run_with_deadline(roles, task, config):
    timer = asyncio.timeout(remaining_task_seconds())
    try:
        async with timer:
            outputs = await run_task(roles, task, config)
            # A synchronous tool can return before the event loop gets to
            # deliver cancellation. Check the monotonic deadline too.
            remaining_task_seconds()
            check_task_cost()
            return outputs
    except TimeoutError as exc:
        if timer.expired():
            raise TaskTimeLimitExceeded() from exc
        raise


def run_one_task(
    config: CrewConfig, ledger: Ledger, task: str, issue_number: int | None = None
) -> int:
    """Run one attempt and checkpoint cumulative spend on every exit."""
    try:
        return _run_one_task(config, ledger, task, issue_number)
    finally:
        _record_spend(ledger)


def _run_one_task(
    config: CrewConfig, ledger: Ledger, task: str, issue_number: int | None = None
) -> int:
    """Run a single task through the crew. Returns the exit code.

    The reviewer is the gate: after the run finishes, the run
    settles as stalled — not finished — when the builder made no
    tool call even after its retries (`builder_no_action`), when
    the reviewer requested changes and nothing was approved, when
    no review was recorded, or when the sign-off ledger could not
    be read. A run that records task_finished with zero PRs is the
    Nov 19 hollow-done mistake; the gate exists to stop it
    repeating. An approved sign-off is what lets a run finish. A
    changes_requested on one PR is a per-PR blocker, not a task
    blocker: a sweep that approved (and merged) other PRs finishes,
    and the blocked PRs stay open for a later task. When supplied,
    `issue_number` identifies the original issue to close with the
    outcome. Stalls also tag it.
    """
    log = logging.getLogger("crew")
    # The task branch is set up in code before the run: the
    # builder never decides about branches, so a branch refusal cannot
    # stall the run (Nov 27, #75). A failure here degrades to the old
    # behaviour — the run continues without the branch line — instead
    # of failing the run. The branch line goes only to the run
    # input; the ledger keeps the original task text, so settlement
    # matching is unchanged.
    try:
        with task_limits(config.task_cost_limit_usd, config.task_time_limit_minutes):
            branch = None
            try:
                branch = ensure_task_branch(task_branch_name(issue_number, task))
                log.info("task branch: %s", branch)
            except TaskLimitExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - branch setup must not kill a run
                log.warning("could not set up task branch, continuing without one: %s", exc)
            finally:
                _record(
                    ledger, "task_started", task=task, repo=config.repo,
                    **({"branch": branch} if branch else {}),
                )
            try:
                before = len(Ledger(config.ledger_path).sign_offs())
            except Exception as exc:  # noqa: BLE001 - ledger must not kill a run
                log.warning("could not read sign-off ledger: %s", exc)
                before = None
            remaining_task_seconds()
            roles = build_roles(config)
            workflow_task = task
            if branch is not None:
                workflow_task = (
                    f"{task}\n\nWork on branch: {branch} (already created and "
                    "checked out for this task — do not create another branch; "
                    "confirm with `git branch --show-current` if unsure)."
                )
            outputs = asyncio.run(_run_with_deadline(roles, workflow_task, config))
    except Exception as exc:  # noqa: BLE001 - report the failure, then exit 1
        fields = {"reason": exc.reason} if isinstance(exc, TaskLimitExceeded) else {}
        _record(ledger, "task_failed", task=task, error=str(exc), **fields)
        log.error("task failed: %s", exc)
        comment = f"Failed: {exc}." if fields else "Failed: the run raised an exception."
        _settle_task_issue(config, issue_number, comment)
        return 1

    # One summary for every settlement path: the header (agents ran,
    # PR, review verdict, first tool failure) plus each agent's
    # response text. The stalled issue for the Nov 20 #9 run carried
    # only the merger's output and said nothing about why the run
    # produced nothing — this is the fix for that.
    sign_offs = _sign_offs_since(config, before) if before is not None else None
    summary = format_run_summary(outputs, sign_offs)
    if builder_tool_calls(outputs) == 0:
        # The builder made no tool call even after its retries, so
        # there is nothing for the reviewer to review. This stalls
        # as builder_no_action, not no_review — no_review would say
        # the review failed when the build never happened. Checked
        # first: a missing sign-off is the consequence here, not
        # the cause. builder_tool_calls returns None when the
        # outputs carry no message structure (plain strings), so
        # those runs fall through to the existing gates below.
        _record(ledger, "task_stalled", task=task, reason="builder_no_action")
        log.warning("task stalled (builder made no tool call):\n%s", summary)
        try:
            url = report_stalled(
                config.repo, task, summary, reason="builder_no_action"
            )
            log.info("stalled issue posted: %s", url)
        except Exception as exc:  # noqa: BLE001 - feedback must not fail the run
            print(f"warning: could not post stalled issue: {exc}", file=sys.stderr)
        _settle_task_issue(
            config, issue_number,
            f"Stalled: builder_no_action\n\n{summary}", stalled=True,
        )
        return 1
    if sign_offs is None:
        # The sign-off ledger could not be read, so no approval can
        # be verified: stall rather than finishing without one. The
        # stall is settled through the close-and-label helper, like
        # the other stalls.
        _record(ledger, "task_stalled", task=task, reason="review_unavailable")
        log.warning("task stalled (review sign-offs unavailable):\n%s", summary)
        try:
            url = report_stalled(
                config.repo, task, summary, reason="review_unavailable"
            )
            log.info("stalled issue posted: %s", url)
        except Exception as exc:  # noqa: BLE001 - feedback must not fail the run
            print(f"warning: could not post stalled issue: {exc}", file=sys.stderr)
        _settle_task_issue(
            config, issue_number,
            f"Stalled: review_unavailable\n\n{summary}", stalled=True,
        )
        return 1
    requested = [
        s for s in sign_offs if s.get("verdict") == "changes_requested"
    ]
    approved = [s for s in sign_offs if s.get("verdict") == "approved"]
    # A changes_requested stalls the task only when nothing was
    # approved in the run. A sweep that approved (and merged)
    # some PRs finishes; the blocked PRs are per-PR blockers,
    # not task blockers, and stay open for a later task.
    if requested and not approved:
        _record(ledger, "task_stalled", task=task, reason="review")
        log.warning("task stalled (reviewer requested changes):\n%s", summary)
        try:
            url = report_stalled(config.repo, task, summary, reason="review")
            log.info("stalled issue posted: %s", url)
        except Exception as exc:  # noqa: BLE001 - feedback must not fail the run
            print(f"warning: could not post stalled issue: {exc}", file=sys.stderr)
        _settle_task_issue(
            config, issue_number, f"Stalled: review\n\n{summary}", stalled=True
        )
        return 1
    if not sign_offs:
        _record(ledger, "task_stalled", task=task, reason="no_review")
        log.warning("task stalled (no review recorded):\n%s", summary)
        try:
            url = report_stalled(config.repo, task, summary, reason="no_review")
            log.info("stalled issue posted: %s", url)
        except Exception as exc:  # noqa: BLE001 - feedback must not fail the run
            print(f"warning: could not post stalled issue: {exc}", file=sys.stderr)
        _settle_task_issue(
            config, issue_number, f"Stalled: no_review\n\n{summary}", stalled=True
        )
        return 1
    _record(ledger, "task_finished", task=task, outputs=outputs)
    log.info("task finished:\n%s", summary)
    try:
        url = report_done(config.repo, task, summary)
        log.info("done issue posted: %s", url)
    except Exception as exc:  # noqa: BLE001 - feedback must not fail the run
        print(f"warning: could not post done issue: {exc}", file=sys.stderr)
    _settle_task_issue(config, issue_number, "Done: the task finished.")
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
        return run_one_task(config, ledger, args.task)

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
        picked = next_task_issue(config, ledger)
        if picked is None:
            log.info("no task queued, sleeping %ss", interval)
            time.sleep(interval)
            continue
        log.info("picked up task: %s", picked["task"][:120])
        run_one_task(config, ledger, picked["task"], issue_number=picked["number"])
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
