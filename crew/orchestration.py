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

Observability (Nov 20): the #9 run finished in 6 supersteps and
stalled with no PR and no review, and nothing in the logs or the
stalled issue said why — the workflow returned only the merger's
output, and tool failures surfaced as opaque RuntimeErrors. So
`output_from=[merger]` with `intermediate_output_from="all_other"`
surfaces every agent's response as a workflow event (the merger's
as the final output, the other three as intermediates), `run_task`
logs each agent's response text at INFO, and `format_run_summary`
builds the header the settlement issues carry (agents ran, PR,
verdict, first tool failure) from the responses plus the sign-offs
recorded during the run.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agent_framework import Workflow
from agent_framework.orchestrations import SequentialBuilder

from .config import ALLOWED_REPO, CrewConfig
from .credit import should_stop, stop_reason
from .roles import build_roles
from .spend import spent_usd

# Pipeline order, used as the fallback agent name when a response
# carries no author_name (fake agents in tests; real agents set it).
_PARTICIPANT_ORDER = ("planner", "builder", "reviewer", "merger")

# The header's first-failure line is a one-liner; the full detail
# lives in its own section below it. Issue bodies cap at 64KB, so a
# 20KB command dump in the header would bury the rest — the cap
# keeps the header scannable.
_FAILURE_LINE_MAX = 500


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
        # Every agent's response surfaces as a workflow event (the
        # merger's as the final output, the other three as
        # intermediates): without this the run returns only the last
        # agent's text and the other three agents' work is invisible
        # in the logs and the issues. output_from must be explicit —
        # with intermediate_output_from="all_other" alone, the
        # framework designates no output at all and every response
        # lands as an intermediate.
        output_from=[roles["merger"]],
        intermediate_output_from="all_other",
    ).build()


def agent_summaries(outputs: list[Any]) -> list[tuple[str, str]]:
    """One (agent name, response text) pair per workflow output.

    The name comes from the response messages' author_name (the
    framework sets it to the agent's name); outputs without one —
    plain strings in tests — fall back to pipeline order. Works on
    any list: intermediates + final, or just the final output.
    """
    summaries = []
    for i, output in enumerate(outputs):
        name = ""
        messages = getattr(output, "messages", None)
        if messages:
            for message in messages:
                if getattr(message, "author_name", None):
                    name = message.author_name
                    break
        if not name:
            name = (
                _PARTICIPANT_ORDER[i]
                if i < len(_PARTICIPANT_ORDER)
                else f"agent {i + 1}"
            )
        summaries.append((name, str(output)))
    return summaries


def first_tool_error(outputs: list[Any]) -> str | None:
    """First tool failure in the run, as "tool: error result".

    Scans the responses' function_result contents for the framework's
    error result ("Error: Function failed...") and pairs it with the
    tool name from the matching function_call (by call_id). The
    detail is there because detailed errors are on (crew/clients.py)
    and _run logs it (crew/tools.py); this is the copy the
    settlement issue carries, so a stalled run says where it broke.
    """
    for output in outputs:
        messages = getattr(output, "messages", None) or []
        names: dict[str, str] = {}
        for message in messages:
            for content in getattr(message, "contents", []) or []:
                if getattr(content, "type", None) == "function_call":
                    names[getattr(content, "call_id", "")] = (
                        getattr(content, "name", "") or "unknown tool"
                    )
        for message in messages:
            for content in getattr(message, "contents", []) or []:
                if getattr(content, "type", None) != "function_result":
                    continue
                result = getattr(content, "result", None)
                if isinstance(result, str) and result.startswith("Error:"):
                    tool = names.get(getattr(content, "call_id", ""), "unknown tool")
                    return f"{tool}: {result}"
    return None


def pr_number_from_outputs(
    outputs: list[Any], sign_offs: list[dict] | None
) -> int | None:
    """The PR number for the run, if any is recorded anywhere.

    Sign-offs first (the reviewer names the PR it reviewed); else
    the open_pr result — the PR URL in a function_result — since a
    run can open a PR and still stall with no review recorded.
    """
    if sign_offs:
        for sign_off in reversed(sign_offs):
            pr_number = sign_off.get("pr_number")
            if isinstance(pr_number, int):
                return pr_number
    pattern = re.compile(
        re.escape(f"github.com/{ALLOWED_REPO}/pull/") + r"(\d+)"
    )
    for output in outputs:
        for message in getattr(output, "messages", None) or []:
            for content in getattr(message, "contents", []) or []:
                if getattr(content, "type", None) != "function_result":
                    continue
                result = getattr(content, "result", None)
                if isinstance(result, str):
                    match = pattern.search(result)
                    if match:
                        return int(match.group(1))
    return None


def format_run_summary(outputs: list[Any], sign_offs: list[dict] | None) -> str:
    """The settlement-issue body for a run: a header plus the narrative.

    Header: which agents ran, the PR number if any, the review
    verdict if any (with notes), and the first tool failure if the
    run produced nothing shippable. `sign_offs=None` means the
    sign-off ledger could not be read at all, so the verdict is
    unknown rather than "none recorded". Below the header, each
    agent's response text in pipeline order — that is the readable
    narrative of the run.
    """
    summaries = agent_summaries(outputs)
    ran = ", ".join(name for name, _ in summaries) or "none"
    pr_number = pr_number_from_outputs(outputs, sign_offs)
    pr_line = f"#{pr_number}" if pr_number is not None else "none recorded"
    if sign_offs is None:
        review_line = "unknown (sign-off ledger unreadable)"
    elif not sign_offs:
        review_line = "none recorded"
    else:
        parts = []
        for sign_off in sign_offs:
            verdict = sign_off.get("verdict", "?")
            prn = sign_off.get("pr_number")
            part = f"{verdict} on PR #{prn}" if isinstance(prn, int) else str(verdict)
            notes = sign_off.get("notes", "")
            if notes:
                part += f": {notes}"
            parts.append(part)
        review_line = "; ".join(parts)
    failure = first_tool_error(outputs)
    failure_line = (
        failure[:_FAILURE_LINE_MAX] + ("…" if len(failure) > _FAILURE_LINE_MAX else "")
        if failure is not None
        else "none"
    )
    header = "\n".join(
        [
            "Run summary:",
            f"- Agents ran: {ran}",
            f"- PR: {pr_line}",
            f"- Review: {review_line}",
            f"- First failure: {failure_line}",
        ]
    )
    sections = [header]
    if failure is not None:
        sections.append(f"First failure detail:\n{failure}")
    for name, text in summaries:
        sections.append(f"{name}:\n{text}" if text else f"{name}: (no text)")
    return "\n\n".join(sections)


async def run_task(workflow: Workflow, task: str, config: CrewConfig) -> list[Any]:
    """Run one task through the crew. Credit/deadline stop is checked
    before any model call; the workflow itself re-checks via its tools.

    The spend figure is the tracker's live total (crew/spend.py), not
    the config's starting offset — the offset only seeds the tracker
    at process start.

    Returns every agent's response in pipeline order (intermediates
    plus the merger's final output), and logs each one at INFO: the
    container log is the run's narrative — what the planner planned,
    what the builder did, what the reviewer decided, what the merger
    did. Before Nov 20 only the merger's output came back, so a run
    that produced nothing left no trace of what the other agents did.
    """
    spent = spent_usd()
    if should_stop(spent, config.budget_limit_usd, config.deadline_iso):
        raise RuntimeError(
            "crew stopped before starting: "
            f"{stop_reason(spent, config.budget_limit_usd, config.deadline_iso)}"
        )
    result = await workflow.run(task)
    outputs = result.get_intermediate_outputs() + result.get_outputs()
    log = logging.getLogger("crew.orchestration")
    for name, text in agent_summaries(outputs):
        log.info("%s response:\n%s", name, text or "(no text)")
    return outputs


__all__ = ["build_workflow", "run_task", "agent_summaries", "first_tool_error", "pr_number_from_outputs", "format_run_summary"]
