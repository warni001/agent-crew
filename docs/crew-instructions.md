# Crew instructions

Operating instructions for the autonomous coding crew in this repo.
You are the crew. There is no human review in the loop: agent
review, tests, and CI are the gates. Read `AGENTS.md` (repo root)
and `ponytail.md` alongside this file.

## Roles

| Role | Model | Job |
|---|---|---|
| Planner | GPT-5.4 Mini | Breaks a task into small, ordered steps. Writes the plan into the task issue. |
| Builder | GPT-5.4 Mini | Implements one step at a time, smallest change that works, with a test. Opens the PR. |
| Reviewer | GPT-5.4 Mini | Reviews the PR against Ponytail and the plan. Sign-off is the merge gate — see below. |
| Merger | GPT-5.4 Mini | Merges only after Reviewer sign-off, only when tests and CI are green. |

One model for all roles (gpt-5.4-mini), no human review. Roles do not blur:
the Builder never approves its own PR, the Merger never merges
without sign-off.

## Gates (all must pass before a merge)

1. **Tests green.** `pytest` offline, mocked chat/`gh`. Failed
   tests do not push, and do not merge.
2. **Reviewer sign-off.** `merge_pr` requires the latest reviewer ledger
   verdict for that PR to be `approved`. A later `changes_requested`
   verdict supersedes an earlier approval. No sign-off, an unreadable
   ledger, or approval for another PR leaves the gate closed.
   This check does not replace the separate CI gate.
3. **Ponytail check.** The Reviewer checks the PR against
   `ponytail.md`: first rung that works, no abstraction for later,
   one small test for non-trivial logic, shortcuts marked
   `shortcut: <limit>, <when to upgrade>`, PR description ends with
   skipped / not checked / risk.
4. **CI green.** The GitHub Actions workflow must pass on the PR.

## Feedback via GitHub issues (never blocks waiting)

- The crew never blocks waiting for a human. If something needs a
  human decision, open an issue describing the question and keep
  working on the next step.
- When a task finishes, post a **"done"** issue: title names the
  task and says done, body carries the summary and the PR link,
  labels include `done`. Example payload shape is pinned by
  `tests/test_feedback.py` (`done_issue_payload`).
- The repo (GitHub issues) is the source of truth and the feedback
  channel. New questions surface there; the crew never waits on a
  reply.

## GHCR flow (read this before touching images)

Your token stays **Actions read-only**. No Actions write access for
the crew is fine — the GHCR push does not go through your token at
all. The flow is:

1. You write code and open PRs in `warni001/agent-crew`.
2. CI merges (after the gates above).
3. GitHub Actions builds the image and pushes it with
   `GITHUB_TOKEN` (`packages: write`):
   `ghcr.io/warni001/agent-crew:sha-<commit-sha>` — the sha tag only,
   never `:latest`.
4. The Container App is updated to the sha-tagged image automatically:
   the deploy job in `crew-image.yml` waits for CI to pass on the
   commit, verifies the sha-tagged manifest resolves, then updates
   the app with a unique revision suffix and verifies the new
   revision reports ready — a push to main deploys automatically.
   Never deploy `:latest`: updating with an unchanged `:latest`
   string mints no new revision, and a `:latest` pull can resolve to
   a stale cached manifest. A sha tag can only ever point at one
   digest. The image for a commit is
   `crew.tools.image_for_commit(<sha>)`. A deploy restarts the
   container, so an in-flight task restarts — finished work is never
   re-run (the "[crew] Done:" issue guard). The manual fallback, if
   the deploy job ever needs bypassing, is:

   ```bash
   az containerapp update -g rg-agent-crew -n ca-agent-crew --image ghcr.io/warni001/agent-crew:sha-<commit-sha> --revision-suffix <suffix>
   ```

You must not attempt a direct GHCR push with your token. A direct
`docker push` with the crew PAT will fail (no Packages permission)
and is forbidden regardless — the push belongs to Actions.

## Ledger (persists across restarts)

The run ledger (task_started / task_finished / task_failed, reviewer
sign-offs) lives in an append blob at `CREW_LEDGER_BLOB_URL`, so a
deploy restart no longer wipes it. When the URL is unset the crew
falls back to the local `crew-ledger.jsonl` file, and a blob failure
warns and falls back too — the ledger never kills a run. Cost is
fractions of a cent at this scale (Hot tier, kilobytes, a handful of
writes per task). One limit to know: an append blob caps at 50,000
appends, so rotate to a new blob when entries approach 45,000.

After the first push, the package must be made public so the
Container App can pull it anonymously (public repo pulls
anonymously; a private package would need credentials the app does
not have).

## Scope lock

You work in `warni001/agent-crew` only — agent-crew only. Your
fine-grained token is scoped to this repo (Contents, Pull requests,
Issues read/write; Actions read), and the tools enforce the same
boundary in code (`crew.tools.ALLOWED_REPOS`). Any operation naming
another repo is rejected before it runs. There are no exceptions.

## Config (environment only)

- `KEY_VAULT_URL` — Key Vault holding `GitHubToken`
- `FOUNDRY_PROJECT_ENDPOINT` — `https://foundry-gpt-vaxvu3c2.services.ai.azure.com/api/projects/crew`
  (the Foundry *project* endpoint, set by bicep; the chat endpoint is
  derived from it — see Module contract below)
- `AZURE_OPENAI_ENDPOINT` — optional override for the chat endpoint
  (`https://foundry-gpt-vaxvu3c2.openai.azure.com`); unset means
  "derive it from `FOUNDRY_PROJECT_ENDPOINT`"
- `GITHUB_TOKEN` — the crew PAT, set from the vault by the Container App
  secret ref (the `gh` CLI reads the same secret as `GH_TOKEN`)

No secrets in code. No `.env` reads. A missing variable is a hard
error naming the variable.

## Stop rules (spend and deadline)

- **Budget:** $200. Stop work at **$190 (95%)**. There is no
  automatic kill — no Cost Management budget, no Action Group, no
  Logic App, no $40 metric alert (the Nov 20 kill switch and its
  template were removed Nov 21, 2026). The only enforcement is the
  crew itself: it counts every call from API-reported usage
  (`crew/spend.py`, `crew/clients.py`) and refuses the next call
  once tracked spend reaches $190. One call can still cross the
  line — the overshoot is bounded by a single call's cost. Stop
  yourself first; nothing in Azure stops you.
- **Hard deadline:** Sat Dec 26 3:08:36pm ET. The crew refuses to
  start past it. It deletes nothing. Stop before it.

## Models

- All roles: GPT-5.4 Mini, version `2026-03-17`, westus3, Data Zone
  Standard (200K TPM quota, verified live Nov 27)
- Endpoint: `https://foundry-gpt-vaxvu3c2.openai.azure.com` (classic
  Azure OpenAI; derived from the Foundry project endpoint
  `https://foundry-gpt-vaxvu3c2.services.ai.azure.com/api/projects/crew`)
- No Claude in this subscription (PAYG ineligible for Claude in
  Foundry). Do not attempt a Claude deployment.
- `agent-framework==1.26.0` exactly — versions 1.26.1/1.26.2 were
  hijacked and removed, never install them.

## First task

Build v1 of yourself in this repo: the crew package, wired to the
classic endpoint above, with the gates, feedback, and scope lock in
this file. Keep improving v1 until the next task arrives through an
issue. The next project after v1 is the ResumeBot multi-tenant
platform (a separate private repo) — it arrives through an issue
when v1 is ready.

Tasks arrive as GitHub issues labelled `crew-task` in this repo; the
crew polls for them every 60 seconds (`CREW_POLL_SECONDS` overrides
the interval) and takes the oldest one it has not already finished
or failed. A task that failed is not retried automatically — it needs
a new issue to re-queue it.

A run is a single pass: planner, builder, reviewer, merger, in
that order, each seeing only the previous agent's response. There
is no round cap and no replanning. The reviewer is the gate: the
merger merges only on an approved sign-off. If the reviewer
requests changes, or no review is recorded at all, the run settles
as stalled, not finished — the crew records `task_stalled` and
posts a `[crew] Stalled:` issue, and the loop does not re-run the
task on its own. Re-queue a stalled task with a refined issue that
addresses the review notes.

## Orchestration (sequential — verified against agent-framework 1.26.0)

- Sequential lives in `agent_framework.orchestrations` — there is
  no `agent_framework.magentic` module.
- Build it with `SequentialBuilder(participants=[...],
  chain_only_agent_responses=True)` (`.build()`). With
  `chain_only_agent_responses=True` each agent receives only the
  previous agent's response messages, not the full conversation —
  that is what keeps each agent's Responses chain free of other
  agents' tool transcripts (a foreign tool result in your chain
  400s: "No tool call found for function call output"). Each agent
  runs with the framework default `store=True`; do not set
  `store=False` — that was the Nov 19 Magentic workaround, and it
  re-sends the full history on every call.
- Agents are `agent_framework.Agent(client, instructions, ...)` —
  the chat client is the first positional argument. There is no
  `ChatAgent` in 1.26.0; do not use that name.

## Module contract (what the tests expect)

The test suite pins this API so the Builder and the tests agree.
Implement to these names; if a name must change, update the tests
in the same PR.

- `crew.config.load_config(env=None) -> Config` with fields
  `key_vault_url`, `foundry_gpt_endpoint`, `github_token`;
  `crew.config.ConfigError(ValueError)` raised naming any missing
  variable; never reads `.env`.
- `crew.budget.BUDGET_USD = 200.0`, `STOP_THRESHOLD_USD = 190.0`,
  `HARD_DEADLINE` = Sat Dec 26 2026 3:08:36pm America/New_York;
  `crew.budget.should_stop(spent_usd, now) -> bool`.
- `crew.gates.REVIEWER_ROLE = "reviewer"`;
  `crew.gates.can_merge(reviews) -> bool` — true only with a
  reviewer approval.
- `crew.feedback.done_issue_payload(task_id, summary, pr_url=None)
  -> dict` with `title` (task + "done"), `body` (summary, PR link),
  `labels` (includes `"done"`).
- `crew.tools.ALLOWED_REPOS = frozenset({"warni001/agent-crew"})`,
  `crew.tools.GHCR_IMAGE = "ghcr.io/warni001/agent-crew:latest"`,
  `crew.tools.assert_repo_allowed(repo)` raising `ValueError`.
- `crew.clients.build_chat_client(endpoint, model, api_version=None,
  client_factory=None)` — factory injectable so tests stay offline.
  The real client is `agent_framework_openai.OpenAIChatClient`,
  keyword-only args `model=...` (deployment name, e.g.
  `gpt-5-4-mini`), `azure_endpoint=...` (the classic Azure OpenAI
  endpoint, `https://foundry-gpt-vaxvu3c2.openai.azure.com`),
  `credential=...`, `api_version="preview"`.
  Chat calls go to the classic endpoint, NOT the Foundry project
  endpoint: proven live Nov 19, the project endpoint returns HTTP
  200 with zero usage and no message output even for "Say hello",
  while the classic endpoint returns real text and the ledger prompt
  returns valid JSON with no format forcing. The endpoint is
  `config.resolve_azure_openai_endpoint()` — the explicit
  `AZURE_OPENAI_ENDPOINT`, else derived from the
  `FOUNDRY_PROJECT_ENDPOINT` bicep already sets — so no bicep change
  is needed. The test only pins the factory-injection seam, never
  the live call.

## How to run locally

Set up Python 3.14 and install the same dependencies as CI. This step
may need network access to download packages.

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest==9.2.0
```

Run the tests offline from the repo root. You do not need Azure or GitHub
credentials.

```bash
.venv/bin/python -m pytest
```

Tests run offline: chat clients and the `gh` CLI are mocked.
No live model calls, no live Azure calls. The full crew needs the
environment variables above plus Azure access; the tests do not.
