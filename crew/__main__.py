"""Entry point: python -m crew "task text"

Loads config from env, validates it, builds the Magentic workflow, runs
one task, records the run in the ledger, and posts a "done" issue update.
A failed feedback post warns on stderr but does not fail the run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import CrewConfig
from .feedback import report_done
from .ledger import Ledger
from .orchestration import build_workflow, run_task


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Quiet the chatty Azure loggers; the root handler captures everything.
    logging.getLogger("azure").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crew", description="Run one crew task.")
    parser.add_argument("task", help="the task for the crew, as one string")
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
    ledger.record("task_started", task=args.task, repo=config.repo)
    try:
        workflow = build_workflow(config)
        outputs = asyncio.run(run_task(workflow, args.task, config))
    except Exception as exc:  # noqa: BLE001 - report the failure, then exit 1
        ledger.record("task_failed", task=args.task, error=str(exc))
        log.error("task failed: %s", exc)
        return 1

    summary = "\n\n".join(str(o) for o in outputs) or "(no output)"
    ledger.record("task_finished", task=args.task, outputs=outputs)
    log.info("task finished:\n%s", summary)
    try:
        url = report_done(config.repo, args.task, summary)
        log.info("done issue posted: %s", url)
    except Exception as exc:  # noqa: BLE001 - feedback must not fail the run
        print(f"warning: could not post done issue: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
