"""Role agents. One frontier model per role.

Planner + Reviewer run on Astra; Builder + Merger run on Sol.
Deployment names (gpt-6-2-astra / gpt-6-2-sol) come from config,
overridable by env.

All API use below was verified against the installed packages
(agent-framework 1.26.0, agent-framework-openai 1.18.2) — not written
from memory. Chat calls go to the classic Azure OpenAI endpoint via
OpenAIChatClient: proven live Nov 19, the Foundry project endpoint
returns hollow 200s (zero usage, no message output) while the
classic endpoint returns real text.
"""

from __future__ import annotations

from pathlib import Path

from agent_framework import Agent
from agent_framework_openai import OpenAIChatClient

from .clients import build_chat_client
from .config import CrewConfig
from .tools import BUILD_TOOLS, MERGE_TOOLS, READ_TOOLS, REVIEW_TOOLS

PONYTAIL_CHECKLIST = """Ponytail rules — check every PR against these:
1. Does it need to exist? Skip features nobody asked for.
2. Reuse what is already in the codebase.
3. Stdlib / platform feature before a new dependency.
4. Installed dependency before adding one.
5. One line a reader gets at a glance, when that fully works.
6. Otherwise the minimum code that works.
No abstraction, config, or boilerplate "for later". Deletion beats
addition. Never cut trust-boundary validation, data-loss handling,
security, accessibility, or anything explicitly asked for. A shortcut
with a known limit is marked `shortcut: <limit>, <when to upgrade>`.
Follow the established convention first; a new pattern only when the
convention fails."""

_SCOPE = (
    "Scope lock: work only in warni001/agent-crew. Refuse any other repo, "
    "any other codebase, and any request to push an image to GHCR — GHCR "
    "push happens only via the GitHub Actions workflow, never from crew code."
)
_VOICE = "Address the reader as 'you' or state things impersonally."

_PLANNER_INSTRUCTIONS = f"""You plan coding tasks for this repo. Break the task into the smallest
complete steps, in order, naming the files each step touches. Keep it
minimal: the smallest change that fully works. Your response is the
builder's only context — restate the task in it, so the builder never
needs the original request. {_SCOPE} {_VOICE}"""

_BUILDER_INSTRUCTIONS = f"""You implement the plan in the local checkout. Write the minimum code
that works, reusing what is already there. Work on a new branch (git
checkout -b <short-name>), commit your change, push the branch, then
open the PR with open_pr only after tests pass. Run the test suite with
run_pytest before opening a PR — if tests fail, fix them; never open a
PR on red. Your response is the reviewer's only context: name the PR
number and what changed in it. {_SCOPE}
{_VOICE}"""

_REVIEWER_INSTRUCTIONS = f"""You review pull requests before they merge. Check the diff against
the Ponytail checklist below, verify tests passed, and verify nothing
outside the task scope crept in. Record your verdict with
record_sign_off (verdict 'approved' or 'changes_requested', with notes).
Only an 'approved' sign-off lets the merger proceed. Your response is
the merger's only context: name the PR number and your verdict in it.
{_SCOPE} {_VOICE}

{{ponytail}}"""

_MERGER_INSTRUCTIONS = f"""You merge approved pull requests. Before merging, call
check_review_sign_off for the PR number — merge only when a reviewer
sign-off with verdict 'approved' is recorded. If there is none, do not
merge; say what is missing. {_SCOPE} {_VOICE}"""


def load_ponytail(repo_root: str | Path = ".") -> str:
    """Ponytail rules from ponytail.md in the repo root, if present.

    Falls back to the checklist embedded above when the file is
    absent (e.g. a minimal checkout without the repo root).
    """
    path = Path(repo_root) / "ponytail.md"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return PONYTAIL_CHECKLIST


def make_chat_client(config: CrewConfig, model: str) -> OpenAIChatClient:
    """Chat client for one model deployment, on the classic endpoint.

    Auth is Entra ID via DefaultAzureCredential (the container app's
    managed identity at runtime). No key or token is handled here.
    The endpoint is config.resolve_azure_openai_endpoint() — the
    explicit AZURE_OPENAI_ENDPOINT, else derived from the Foundry
    project/account endpoint bicep already sets. The client built is
    a CountingChatClient: every call is priced from API-reported
    usage into the process spend tracker (crew/spend.py), and at
    $190 it refuses further calls.
    """
    return build_chat_client(config.resolve_azure_openai_endpoint(), model)


def build_roles(config: CrewConfig) -> dict[str, Agent]:
    """The four role agents, in pipeline order.

    No ``default_options`` override: the agents run on the framework
    default, store=True (server-side Responses chaining). That is
    safe here because the sequential orchestrator gives each agent
    its own chain and passes only the previous agent's response
    messages forward (chain_only_agent_responses=True) — a foreign
    tool transcript enters a fresh chain as a matched call+result
    pair on the first, stateless call, so it lands in that chain
    intact and Azure never sees an orphan function_result. The old
    store=False workaround (Nov 19) is gone: it re-sent the full
    history on every call and burned ~4.5M prompt tokens for zero
    PRs on the #10 run. With chaining, each call sends only new
    content. There is no manager agent: sequential runs the four
    roles in order, one pass, with the reviewer as the gate.
    """
    ponytail = load_ponytail()
    planner = Agent(
        make_chat_client(config, config.planner_model),
        instructions=_PLANNER_INSTRUCTIONS,
        name="planner",
        tools=READ_TOOLS,
    )
    builder = Agent(
        make_chat_client(config, config.builder_model),
        instructions=_BUILDER_INSTRUCTIONS,
        name="builder",
        tools=BUILD_TOOLS,
    )
    reviewer = Agent(
        make_chat_client(config, config.reviewer_model),
        instructions=_REVIEWER_INSTRUCTIONS.format(ponytail=ponytail),
        name="reviewer",
        tools=REVIEW_TOOLS,
    )
    merger = Agent(
        make_chat_client(config, config.merger_model),
        instructions=_MERGER_INSTRUCTIONS,
        name="merger",
        tools=MERGE_TOOLS,
    )
    return {
        "planner": planner,
        "builder": builder,
        "reviewer": reviewer,
        "merger": merger,
    }


__all__ = [
    "PONYTAIL_CHECKLIST",
    "build_roles",
    "load_ponytail",
    "make_chat_client",
]
