"""Sequential orchestration: planner -> builder -> reviewer -> merger,
one pass, each agent seeing only the previous agent's response.

Wiring verified against the installed agent-framework 1.26.0 package:
`run_task` runs the four role agents directly, in order, with
`await agent.run(messages, session=agent.create_session())` — each
agent on its own fresh session, each receiving only the previous
agent's response messages as its input. That is the same "last
agent" message passing the old SequentialBuilder provided with
`chain_only_agent_responses=True`, now in plain code — because the
builder needs a gate between its turn and the reviewer's, and a
fixed workflow chain has nowhere to put one.

Why sequential, and why last-agent-only messages: Magentic shares
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

The builder is the one exception to single-pass (Nov 27, #75):
its instructions say never to refuse without calling a tool first,
and four stalled runs proved prose alone cannot make it act — it
returned an empty response with zero tool calls twice, twice with
a false excuse. So `run_task` counts the function calls in the
builder's response and, when there are none, re-invokes the builder
on the same session with a forced nudge, up to two retries. If it
still makes no call, the run stalls with reason `builder_no_action`
(see __main__.py) instead of the misleading `no_review`. The cap
is a spend bound, not a tuning knob: each retry is a model call.

Observability (Nov 20): the #9 run finished in 6 supersteps and
stalled with no PR and no review, and nothing in the logs or the
stalled issue said why — the workflow returned only the merger's
output, and tool failures surfaced as opaque RuntimeErrors. So
`run_task` returns every agent's response in pipeline order,
logs each agent's response text at INFO, and `format_run_summary`
builds the header the settlement issues carry (agents ran, PR,
verdict, first tool failure) from the responses plus the sign-offs
recorded during the run.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agent_framework import Content, Message

from .config import ALLOWED_REPO, CrewConfig
from .credit import should_stop, stop_reason
from .spend import spent_usd

# Pipeline order, used as the fallback agent name when a response
# carries no author_name (fake agents in tests; real agents set it).
_PARTICIPANT_ORDER = ("planner", "builder", "reviewer", "merger")

# The header's first-failure line is a one-liner; the full detail
# lives in its own section below it. Issue bodies cap at 64KB, so a
# 20KB command dump in the header would bury the rest — the cap
# keeps the header scannable.
_FAILURE_LINE_MAX = 500


# Builder action gate (Nov 27, #75): the builder gets at most this
# many attempts per run — the first plus two retries. The cap is a
# spend bound: each retry is a model call, and an unbounded loop
# would let a stuck builder burn the budget one empty turn at a
# time. Two retries give the model a real second chance; if it
# still makes no tool call, the run stalls as builder_no_action.
_BUILDER_MAX_ATTEMPTS = 3

# The forced nudge on a builder retry. It names the concrete first
# action instead of asking again — the plan is already in the
# builder's session from its first turn, so "the plan above" refers
# to it. The last sentence restates the instruction the empty
# response already violated; the gate, not the wording, is what
# enforces it.
_BUILDER_RETRY_NUDGE = (
    "Your previous response made no tool calls. Call read_file on "
    "the first file the planner named in the plan above, then "
    "implement it, run the tests, commit, push, and open the PR. "
    "Do not answer in prose without calling a tool first."
)


def count_tool_calls(response: Any) -> int:
    """Function calls in one agent response, by its messages.

    Counts `function_call` contents across `response.messages`;
    a response with no messages (an empty turn) counts zero. The
    same scan `first_tool_error` uses for results, without the
    error pairing.
    """
    count = 0
    for message in getattr(response, "messages", None) or []:
        for content in getattr(message, "contents", []) or []:
            if getattr(content, "type", None) == "function_call":
                count += 1
    return count


def builder_tool_calls(outputs: list[Any]) -> int | None:
    """Tool calls the builder made in this run — or None when that
    cannot be determined.

    None means the builder's response carries no message structure
    at all (plain-string outputs, as mocked run_task returns in
    other tests): "no calls" cannot be told apart from "unknown",
    so the caller must fall through to the existing no_review
    logic instead of claiming builder_no_action. A real agent
    response always has `.messages` (possibly empty), so in
    production this returns a real count — zero for the empty turn
    that stalled #75 four times.
    """
    summaries = agent_summaries(outputs)
    for i, (name, _) in enumerate(summaries):
        if name != "builder":
            continue
        response = outputs[i]
        if getattr(response, "messages", None) is None:
            return None
        return count_tool_calls(response)
    return None


def _input_messages(response: Any) -> list[Any]:
    """The messages a downstream agent receives from a response.

    The previous agent's response messages, verbatim — the matched
    call+result pairs stay paired, which is what keeps the next
    agent's Responses chain free of orphan function_results (the
    400 the old SequentialBuilder's "last_agent" mode prevented).
    A response with no messages falls back to its text as a single
    user message, so the pipeline keeps moving instead of crashing
    on an unexpected shape.
    """
    messages = getattr(response, "messages", None)
    if messages:
        return list(messages)
    return [
        Message(role="user", contents=[Content.from_text(str(response))])
    ]


async def _run_builder_with_retries(
    agent: Any, session: Any, first_response: Any, log: logging.Logger
) -> Any:
    """Run the builder's turn, re-invoking it while it makes no
    tool call — up to _BUILDER_MAX_ATTEMPTS attempts total.

    The retry runs on the same session, so the builder keeps the
    task, the plan, and its own empty turn in context; only the
    nudge is new. Each attempt is logged, so the container log
    shows whether the gate fired and what the retry returned.
    """
    response = first_response
    attempts = 1
    while count_tool_calls(response) == 0 and attempts < _BUILDER_MAX_ATTEMPTS:
        log.warning(
            "builder made no tool calls (attempt %s/%s); re-invoking",
            attempts,
            _BUILDER_MAX_ATTEMPTS,
        )
        response = await agent.run(
            messages=[
                Message(
                    role="user",
                    contents=[Content.from_text(_BUILDER_RETRY_NUDGE)],
                )
            ],
            session=session,
        )
        attempts += 1
    if count_tool_calls(response) == 0:
        log.warning(
            "builder made no tool calls after %s attempts",
            _BUILDER_MAX_ATTEMPTS,
        )
    return response


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
    the result matched to an open_pr call by call_id, since a
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
        messages = getattr(output, "messages", None) or []
        open_pr_call_ids = {
            content.call_id
            for message in messages
            for content in getattr(message, "contents", []) or []
            if getattr(content, "type", None) == "function_call"
            and getattr(content, "name", None) == "open_pr"
            and getattr(content, "call_id", None)
        }
        for message in messages:
            for content in getattr(message, "contents", []) or []:
                if getattr(content, "type", None) != "function_result":
                    continue
                if getattr(content, "call_id", None) not in open_pr_call_ids:
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


async def run_task(
    roles: dict[str, Any], task: str, config: CrewConfig
) -> list[Any]:
    """Run one task through the crew, planner -> builder -> reviewer
    -> merger, in that order. `roles` is the dict `build_roles`
    returns. Credit/deadline stop is checked before any model call;
    the agents themselves re-check via their tools.

    Each agent runs on its own fresh session and receives only the
    previous agent's response messages — the same last-agent-only
    passing the old SequentialBuilder did, which is what keeps a
    foreign tool transcript out of the next agent's chain. The
    planner gets the task string; every later agent gets the
    previous response's messages verbatim (matched call+result
    pairs intact, never an orphan function_result).

    The spend figure is the tracker's live total (crew/spend.py), not
    the config's starting offset — the offset only seeds the tracker
    at process start.

    Returns every agent's response in pipeline order, and logs each
    one at INFO: the container log is the run's narrative — what the
    planner planned, what the builder did, what the reviewer decided,
    what the merger did. Before Nov 20 only the merger's output came
    back, so a run that produced nothing left no trace of what the
    other agents did.
    """
    spent = spent_usd()
    if should_stop(spent, config.budget_limit_usd, config.deadline_iso):
        raise RuntimeError(
            "crew stopped before starting: "
            f"{stop_reason(spent, config.budget_limit_usd, config.deadline_iso)}"
        )
    log = logging.getLogger("crew.orchestration")
    outputs: list[Any] = []
    previous: Any = None
    for name in _PARTICIPANT_ORDER:
        agent = roles[name]
        session = agent.create_session()
        if previous is None:
            # The task enters as one user message — the same
            # normalization the old workflow's input step did.
            response = await agent.run(
                messages=[
                    Message(
                        role="user",
                        contents=[Content.from_text(task)],
                    )
                ],
                session=session,
            )
        else:
            response = await agent.run(
                messages=_input_messages(previous), session=session
            )
        if name == "builder":
            response = await _run_builder_with_retries(
                agent, session, response, log
            )
        outputs.append(response)
        previous = response
    for name, text in agent_summaries(outputs):
        log.info("%s response:\n%s", name, text or "(no text)")
    return outputs


__all__ = ["run_task", "agent_summaries", "count_tool_calls", "builder_tool_calls", "first_tool_error", "pr_number_from_outputs", "format_run_summary"]
