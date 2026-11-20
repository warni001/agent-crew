"""Sequential orchestration: planner -> builder -> reviewer -> merger,
one pass, each agent seeing only the previous agent's response.

Wiring verified against the installed agent-framework 1.26.0 package:
SequentialBuilder(participants=[...], chain_only_agent_responses=True)
.build() returns a Workflow; `await workflow.run(task)` returns a
WorkflowRunResult whose outputs come from get_outputs() (the last
participant, the merger, by default).

Why sequential, and why chain_only_agent_responses: Magentic shares
the full chat history — including every participant's tool transcript
— with every other participant, but each agent owns a separate
Responses-API chain. Under chaining the client strips a foreign
``function_call`` yet still emits its ``function_result``, and Azure
400s ("No tool call found for function call output"). The Nov 19
workaround was store=False on every agent (stateless full-history
replay per call); it worked but burned ~4.5M prompt tokens for zero
PRs on the #10 run and kept tripping 429s. Sequential with
chain_only_agent_responses=True passes only the previous agent's
response messages to the next agent (AgentExecutor context_mode
"last_agent"), and each executor owns its own AgentSession, fresh
per run. A foreign tool transcript therefore enters the next agent's
chain as a matched call+result pair on its first, stateless call, so
it lands in that chain intact — no orphan output, no 400 — and
chaining (store=True, the framework default) is safe again, so each
call sends only new content instead of the whole history.

Single pass, by design (Nov 20): there is no manager and no
replanning — the reviewer is a gate, and the merger merges only on
an approved sign-off. If the reviewer requests changes, or no review
is recorded at all, the run settles as stalled (see __main__.py) so
a human re-queues the task with the notes addressed. A loop-back
(reviewer -> builder) is the known next step if single-pass stalls
too often; it is not built here. One consequence of chaining only
agent responses: a downstream agent never sees the original task,
only the previous agent's response — so each role's instructions
tell it to carry the handoff (task, files, PR number, verdict) in
its reply.
"""

from __future__ import annotations

from typing import Any

from agent_framework import Workflow
from agent_framework.orchestrations import SequentialBuilder

from .config import CrewConfig
from .credit import should_stop, stop_reason
from .roles import build_roles
from .spend import spent_usd


def build_workflow(config: CrewConfig) -> Workflow:
    """Build the sequential workflow for one crew run."""
    roles = build_roles(config)
    return SequentialBuilder(
        participants=[
            roles["planner"],
            roles["builder"],
            roles["reviewer"],
            roles["merger"],
        ],
        chain_only_agent_responses=True,
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
