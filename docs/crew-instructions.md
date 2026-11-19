# Crew instructions

Operating instructions for the autonomous coding crew in this repo.
You are the crew. There is no human review in the loop: agent
review, tests, and CI are the gates. Read `AGENTS.md` (repo root)
and `ponytail.md` alongside this file.

## Roles

| Role | Model | Job |
|---|---|---|
| Planner | GPT-6.2 Astra | Breaks a task into small, ordered steps. Writes the plan into the task issue. |
| Builder | GPT-6.2 Sol | Implements one step at a time, smallest change that works, with a test. Opens the PR. |
| Reviewer | GPT-6.2 Astra | Reviews the PR against Ponytail and the plan. Sign-off is the merge gate — see below. |
| Merger | GPT-6.2 Sol | Merges only after Reviewer sign-off, only when tests and CI are green. |

One frontier model per role, no human review. Roles do not blur:
the Builder never approves its own PR, the Merger never merges
without sign-off.

## Gates (all must pass before a merge)

1. **Tests green.** `pytest` offline, mocked Foundry/`gh`. Failed
   tests do not push, and do not merge.
2. **Reviewer sign-off.** At least one review with role `reviewer`
   and `approved: true`. Builder, Planner, or Merger approval does
   not open the gate. A Reviewer rejection keeps the gate closed.
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
   `ghcr.io/warni001/agent-crew:latest` and
   `ghcr.io/warni001/agent-crew:sha-<commit-sha>`.
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
  (the Foundry *project* endpoint — the bare account endpoint
  `https://foundry-gpt-vaxvu3c2.cognitiveservices.azure.com/` is rejected
  by config validation; `FOUNDRY_GPT_ENDPOINT` is kept only as a fallback
  name for the same project endpoint value)
- `GITHUB_TOKEN` — the crew PAT, set from the vault by the Container App
  secret ref (the `gh` CLI reads the same secret as `GH_TOKEN`)

No secrets in code. No `.env` reads. A missing variable is a hard
error naming the variable.

## Stop rules (spend and deadline)

- **Budget:** $200. Stop work at **$190 (95%)** — the kill-switch
  budget fires at 95% and deletes the resource group. Cost data
  lands 8–24h late, so $190 is a backstop, not a real-time cutoff:
  stop yourself first.
- **Hard deadline:** Sat Nov 21 5pm ET. The kill-switch Logic App
  fires then whether or not the budget fired. Stop before it.

## Models

- Planner + Reviewer: GPT-6.2 Astra, version `2026-11-17`, westus3
- Builder + Merger: GPT-6.2 Sol, version `2026-11-17`, westus3
- Endpoint: `https://foundry-gpt-vaxvu3c2.services.ai.azure.com/api/projects/crew` (project `crew`)
- No Claude in this subscription (PAYG ineligible for Claude in
  Foundry). Do not attempt a Claude deployment.
- `agent-framework==1.26.0` exactly — versions 1.26.1/1.26.2 were
  hijacked and removed, never install them.

## First task

Build v1 of yourself in this repo: the crew package, wired to the
Foundry endpoint above, with the gates, feedback, and scope lock in
this file. Keep improving v1 until the next task arrives through an
issue. The next project after v1 is the ResumeBot multi-tenant
platform (a separate private repo) — it arrives through an issue
when v1 is ready.

Tasks arrive as GitHub issues labelled `crew-task` in this repo; the
crew polls for them every 60 seconds (`CREW_POLL_SECONDS` overrides
the interval) and takes the oldest one it has not already finished
or failed. A task that failed is not retried automatically — it needs
a new issue to re-queue it.

## Orchestration (Magentic — verified against agent-framework 1.26.0)

- Magentic lives in `agent_framework.orchestrations` — there is no
  `agent_framework.magentic` module.
- `MagenticOrchestrator(manager, participant_registry, *,
  require_plan_signoff=False)`; build it with `MagenticBuilder`
  (`.build()`, `.with_checkpointing()`, `.with_plan_review()`).
- The manager is a `StandardMagenticManager` (or a
  `MagenticManagerBase` subclass).
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
  `HARD_DEADLINE` = Sat Nov 21 2026 5pm America/New_York;
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
- `crew.clients.build_foundry_client(endpoint, client_factory=None)`
  — factory injectable so tests stay offline. The real client is
  `agent_framework_foundry.FoundryChatClient`, keyword-only args
  `project_endpoint=...`, `model=...` (deployment name, e.g.
  `gpt-6.2-astra`), `credential=...`.
  `FoundryChatClient` takes a Foundry *project* endpoint, so
  `FOUNDRY_PROJECT_ENDPOINT` is what the container is given (see
  Config above); the bare account endpoint does not work here.
  UNVERIFIED live: the project endpoint and the deployment names
  have not yet been exercised against a live Foundry call — the
  first live run is the verification. The test only pins the
  factory-injection seam, never the live call.

## How to run locally

```bash
python3.14 -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -v
```

Tests run offline: Foundry clients and the `gh` CLI are mocked.
No live model calls, no live Azure calls. The full crew needs the
environment variables above plus Azure access; the tests do not.
