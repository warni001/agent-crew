"""Magentic orchestration: planner -> builder -> reviewer -> merger,
coordinated by a manager agent.

Wiring verified against the installed agent-framework 1.26.0 package:
MagenticBuilder(participants=[...], manager=StructuredLedgerManager(...),
max_round_count=...) .build() returns a Workflow; `await workflow.run(task)`
returns a WorkflowRunResult whose outputs come from get_outputs(). The
manager is the structured-output subclass in crew/manager.py: the plain
manager returns reasoning-only empty text to the ledger prompt, so the
ledger is forced to a JSON schema. No live run has been made — the full
loop is untested against Foundry.
"""

from __future__ import annotations

from typing import Any

from agent_framework import Workflow
from agent_framework.orchestrations import MagenticBuilder

from .config import CrewConfig
from .credit import should_stop, stop_reason
from .manager import StructuredLedgerManager
from .roles import build_roles
from .spend import spent_usd


def build_workflow(config: CrewConfig) -> Workflow:
    """Build the Magentic workflow for one crew run."""
    roles = build_roles(config)
    return MagenticBuilder(
        participants=[
            roles["planner"],
            roles["builder"],
            roles["reviewer"],
            roles["merger"],
        ],
        manager=StructuredLedgerManager(roles["manager"]),
        max_round_count=config.max_rounds,
    ).build()


async def run_task(workflow: Workflow, task: str, config: CrewConfig) -> list[Any]:
    """Run one task through the crew. Credit/deadline stop is checked
    before any model call; the workflow itself re-checks via its tools.

    The spend figure is the tracker's live total (crew/spend.py), not
    the config's starting offset — the offset only seeds the tracker
    at process start.
    """
    spent = spent_usd()
    if should_stop(spent, config.budget_limit_usd, config.deadline_iso):
        raise RuntimeError(
            "crew stopped before starting: "
            f"{stop_reason(spent, config.budget_limit_usd, config.deadline_iso)}"
        )
    result = await workflow.run(task)
    return result.get_outputs()


__all__ = ["build_workflow", "run_task"]
