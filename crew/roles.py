"""Role agents. All four roles run on gpt-5.4-mini — the only GPT model
with TPM quota on this subscription (200K TPM, Data Zone Standard).
The deployment name (gpt-5-4-mini) comes from config, overridable
by env.

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
minimal: the smallest change that fully works. Before planning each
step, check for a branch that already contains the change: list
branches with list_branches and open PRs with list_open_prs. If one
exists, name the branch (and any open PR for it) in your plan; if none
exists, say so. Skip any step already merged or covered by an open PR.
Your response is the builder's only context — restate the task in it,
so the builder never needs the original request. {_SCOPE} {_VOICE}"""

_BUILDER_INSTRUCTIONS = f"""You implement the plan in the local checkout. Write the minimum code
that works, reusing what is already there. Use the exact file paths
the planner named; if a read of one fails, try it with the `crew/`
prefix before concluding it is missing.

Step 1 — get on a branch before writing any code. You must never
implement on main, and creating the branch is part of the task: you
do not need to be asked again. Before creating a branch for a change,
check whether a branch already contains it: list branches with
list_branches (or git ls-remote --heads origin), and if the planner
named one, fetch it and compare its diff against main (git fetch
origin <branch>, git diff main...<branch>). If an existing branch
holds the change, verify it is based on current main, its diff is the
described change and not already merged, and no equivalent PR is
already open (list_open_prs) — then open the PR from that branch
instead of creating a new one. If a PR is already open on that branch
and the branch is behind current main, update the existing branch
from main and push the updated branch, so CI runs on the new head;
do not create a fresh branch for a PR that already exists. A local
update that is never pushed creates no new head and does not count.
If the branch is missing, stale with no open PR, or already merged,
create a fresh branch right away: work on a new branch
(git checkout -b <short-name>), and do not write any code until you
are on it.

Step 2 — implement, commit, push, open the PR. Run the test suite
with run_pytest before opening a PR — if tests fail, fix them; never
open a PR on red. Commit your change, push the branch, then open the
PR with open_pr only after tests pass. Your response is the reviewer's
only context: name the PR number and what changed in it. {_SCOPE}
{_VOICE}"""

_REVIEWER_INSTRUCTIONS = f"""You review pull requests before they merge. First find the PR to
review: list open PRs with list_open_prs and take the PR number from
that listing — the builder's PR is the open one for this task. Never
take a PR number from prose alone, and never guess one. If your
context names no PR number and list_open_prs shows no open PR for
this task, there is nothing to review: say 'no PR to review' and
record nothing — do not call read_pr or record_sign_off. Otherwise
read the PR's description and diff with read_pr first — that is how
you see what changed and what the author claims. Check the diff against
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
merge; say what is missing. Judge each PR on its own sign-off: a
changes_requested verdict on another PR never blocks merging this one.
{_SCOPE} {_VOICE}"""


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
