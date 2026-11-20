# Agent Crew

An autonomous multi-agent coding crew on Microsoft Agent Framework
(`agent-framework` 1.26.0, pinned — never `latest`), running on Azure
and burning the remaining Azure free credit before it expires
Sat Nov 21, ~6:30pm ET. No human review in the loop: agent review,
tests, and CI are the gates. The crew's first task is to build v1 of
itself in this repo.

**Status: scaffold.** This repo currently holds infrastructure only.
The crew code does not exist yet — building it is project #1.

Docs and code in this repo address you as "you" — never in the third
person.

## Layout

```
infra/
  main.bicep            Azure resources (Foundry accounts, Container Apps,
                        Key Vault, Log Analytics, kill switch)
  killswitch-role.bicep subscription-scoped module: the narrow custom role
                        the kill-switch Logic App uses
  main.bicepparam       parameter file (reads CLAUDE_* and BUDGET_* from
                        the environment)
  deploy.sh             deploy from your Mac (where `az` lives)
scripts/
  crew-diag.sh          container revisions, logs, PRs, issues, token usage
  crew-spend.sh         live spend from the blob ledger (guard: $190)
  rollback_watch.py     post-deploy health watch: rolls the new revision
                        back to the last good one on crash-loop or silence
  v1-probe.py           frozen Nov 19 probe that proved api-version=preview
                        on the /openai/v1 path — Magentic-era, not runnable
```

## Deploy

Prereqs: Azure CLI signed in (`az login`), Bicep (the script installs
it if missing), `gh` authenticated (optional — without it the script
prints the deploy credential for manual copy).

Four things need you before (or right after) the deploy:

1. **Quota.** Check TPM quota in the portal (Foundry → Quotas) for
   `gpt-6.2-astra` and `gpt-6.2-sol` in westus3 and for
   `claude-sonnet-5-5` in eastus2. Quota is the likeliest deploy
   blocker: a model deployment fails when quota is zero. `deploy.sh`
   runs a warn-only quota pre-check; if a model has no quota, request
   it in the portal, or re-run with `deployModels=false` and add the
   models later.
2. **Claude attestation.** The Claude deployment sends an organization
   name, country code, and industry to Anthropic as the marketplace
   attestation — it has to name the real organization. Set
   `CLAUDE_ORGANIZATION_NAME` in a repo-root `.env` (never committed)
   before deploying, or the Claude deployment is skipped with a
   warning. Claude also needs an Enterprise or MCA-E subscription per
   the Anthropic Commercial Terms; on any other subscription type the
   Claude deployment is the most likely to fail.
3. **GitHub token.** Create the crew's fine-grained PAT in the GitHub
   web UI (Settings → Developer settings → Personal access tokens →
   Fine-grained tokens) — there is no CLI command for this. Name it
   `agent-crew-runtime`, scope it to `agent-crew` only, with Contents,
   Pull requests, and Issues read/write plus Actions read. Put it in
   `GITHUB_CREW_TOKEN` in `.env` and `deploy.sh` stores it in the Key
   Vault as `GitHubToken`; without it the script warns and skips, and
   the crew cannot reach GitHub until it is stored.
4. **Run the deploy.**
   ```
   ./infra/deploy.sh
   ```
   It creates the resource group first, then the deploy service
   principal `github-agent-crew-deploy` (Contributor on the crew
   resource group; its credential is reset on every re-run and set as
   `AZURE_DEPLOY_CREDENTIALS` in this repo), then the Bicep deploy.
   Re-running is safe for a fresh setup — the deployment is
   incremental. **It is not safe against a live crew:** `main.bicep`
   declares the container app with the placeholder image, so a
   re-run resets the live app's image back to helloworld and mints a
   new revision, killing the running crew until the next push. To add
   infrastructure to a live crew, deploy a standalone template
   instead — see the metric alerts below.

## Kill switch (the spend cutoff)

Azure has no native spend cap on pay-as-you-go — budgets only alert.
So the cutoff is wired, not assumed:

- A **$200 Cost Management budget** on the crew resource group.
  50% and 90% notify by email only. **95% (~$190) fires an Action
  Group**, which calls a **Logic App** that deletes the crew resource
  group. Shutdown is a delete, not a pause.
- A **Recurrence trigger** in the same Logic App fires at the hard
  deadline, **Sat Nov 21 5pm ET**, whether or not the budget fired.
  Deleting the group also deletes the Logic App, so it never fires
  twice.
- The Logic App's managed identity holds a **custom role with exactly
  one permission** (`Microsoft.Resources/subscriptions/resourceGroups/delete`)
  at subscription scope — deleting a resource group is a
  subscription-level operation, so the permission cannot be scoped to
  one group. The blast radius is bounded by the workflow, which
  hardcodes the crew resource group name in the DELETE URI. See
  `infra/killswitch-role.bicep` for the full trade-off.
- **Manual kill, any time:** `az group delete --name rg-agent-crew --yes --no-wait`

## Real-time cost guard

The budget above is a backstop, not a real-time cutoff — Cost
Management data lands 8–24h late. Two faster layers sit in front
of it:

- **Metric alerts on token usage (primary).** `infra/alerts.bicep`
  deploys four metric alert rules on the Foundry account — input and
  output tokens per deployment (`gpt-6-2-astra`, `gpt-6-2-sol`) —
  each set at $40 of tokens in a rolling 24h window, firing the same
  Action Group → the same Logic App. Worst case at first fire: just
  under 4 × $40 ≈ $160 plus a few minutes of alert latency, under
  the $190 stop. Evaluation runs every minute; metric data lands in
  minutes, not hours. Deploy it standalone (it touches only alert
  rules, never the container app):
  ```
  az deployment group create -g rg-agent-crew \
    --template-file infra/alerts.bicep
  ```
  Or one alert rule by hand:
  ```
  az monitor metrics alert create -g rg-agent-crew \
    -n astra-input-40usd --scopes <foundry-account-id> \
    --condition "total ProcessedPromptTokens > 5000000 where ModelDeploymentName includes gpt-6-2-astra" \
    --window-size 1d --evaluation-frequency 1m --severity 0 \
    --action <action-group-id>
  ```
  Honest limit: a 24h window cannot see spend spread over several
  days — a slow drip is still caught by the budget, the deadline,
  and the in-code tracker below.
- **Token counting in the crew (second layer).** Every model call
  goes through `CountingChatClient` (`crew/clients.py`), which
  prices the call from the usage the API reports on the response —
  input, cached-input, and output tokens per model — and accumulates
  it in `crew/spend.py`. The running total persists as `spend`
  events in the run ledger, so a restart picks up where the last run
  left off. When the total reaches $190 the client refuses the next
  call before it is made, and the loop stops itself. One call can
  still cross the line (the check runs before the call, not after),
  so the overshoot past $190 is bounded by a single call's cost.

Honest limit: Cost Management evaluates budgets roughly every 24
hours on cost data that lands 8–24 hours late, so the 95% trigger is
a backstop, not a real-time cutoff. The 95% threshold (not 100%)
leaves headroom for that lag, and the Recurrence trigger is the hard
deadline that does not depend on cost data at all.

## Compute: Container Apps, not a VM

The crew runs as one always-on replica (`minReplicas: 1`,
`maxReplicas: 1`) in Azure Container Apps. A VM would need a VNet,
NIC, public IP, NSG, SSH keys, patching, and a bootstrap script —
more moving parts, more ways for a first deploy to fail. Container
Apps needs none of that: the environment plus the app, logs in Log
Analytics, and the SystemAssigned identity reads the vault and calls
the models with Entra ID. Cost is a rounding error either way
(~$0.40/day at 0.5 vCPU / 1 GiB). The app starts on a public
placeholder image (`mcr.microsoft.com/azuredocs/containerapps-helloworld`);
shipping the crew's own image (GHCR, public repo pulls anonymously)
is the crew's first task.

## Models

| Model | Version | Region | Deployment | Verified against |
|---|---|---|---|---|
| `gpt-6.2-astra` | `2026-11-17` | westus3 | Global Standard | Foundry models catalog (Microsoft Learn) |
| `gpt-6.2-sol` | `2026-11-17` | westus3 | Global Standard | Foundry models catalog (Microsoft Learn) |
| `claude-sonnet-5-5` | `1` (param) | eastus2 | Global Standard | Claude on Foundry starter kit (Microsoft Learn sample) + Anthropic Foundry guide |

Two Foundry accounts because the families do not share a region:
GPT-6.2 is available in westus3 but not eastus2 (Foundry region
table); eastus2 hosts all three Claude families. Capacities are 100
(thousands of TPM) per deployment — quota is 1000K TPM Global
Standard in westus3 for both GPT-6.2 models, verified Nov 19.

Open questions, flagged in the template rather than guessed:

- **Claude model version.** The starter kit defaults to `1`. The
  portal model card lists the hosting option per version (version 1
  for Anthropic-hosted, version 2 for Azure-hosted, per the Anthropic
  guide). If version `1` lands the Anthropic-hosted variant instead
  of the Azure-hosted one, redeploy with `claudeModelVersion=2`.
- **Claude subscription eligibility.** See the deploy section above.

## Verification status

Checked before this push (Nov 19):

- `bicep build` and `bicep lint` pass on both templates (Bicep CLI
  0.49.2). One warning remains: BCP037, `modelProviderData` not in
  the Bicep type for deployments. That property is required — it is
  how the marketplace offer gets auto-signed — and Microsoft's own
  starter kit emits the identical warning with the same Bicep
  version. The property is present in the compiled ARM JSON.
- Compiled ARM JSON inspected: all 19 resources present (vault,
  Log Analytics, 2 Foundry accounts, 2 projects, 3 deployments,
  Container Apps environment + app, 4 role assignments, Logic App,
  Action Group, budget, kill-switch module).
- `shellcheck` clean on `deploy.sh`.
- Model names, versions, and regions checked against Microsoft Learn
  and the Claude on Foundry starter kit (see the table above).
- `agent-framework==1.26.0` resolves cleanly on Python 3.14 (dry-run
  install); the wheel hash matches PyPI. Versions 1.26.1/1.26.2 were
  hijacked and removed — never install them.

Still needs a live deploy to confirm (no `az` on the build machine,
and nothing here has touched Azure yet):

- TPM quota for claude-sonnet-5-5 in eastus2 (GPT-6.2 quota verified:
  1000K TPM Global Standard in westus3).
- Claude subscription eligibility (Enterprise / MCA-E).
- The Claude version/hosting question above.
- The Logic App end-to-end: budget → Action Group → callback URL →
  resource-group delete. The `listCallbackUrl` wiring follows
  Microsoft's documented pattern, but only a live fire proves it.
- The subscription-scoped module (custom role + assignment).

## How to run locally

```bash
python3.14 -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -v
```

Tests run offline: the chat clients and the `gh` CLI are mocked
(`unittest.mock`, see `tests/conftest.py`). No live model calls, no
live Azure calls. The full crew needs `KEY_VAULT_URL`,
`FOUNDRY_PROJECT_ENDPOINT` (the Foundry project endpoint, from which
the classic Azure OpenAI chat endpoint is derived — or set
`AZURE_OPENAI_ENDPOINT` directly), and `GITHUB_TOKEN` from the environment;
the tests do not. The crew package (`crew/`) is built by a parallel
track — tests that need it skip until it merges, and the skip list
names the blocking module.

## How the crew starts

With the runtime environment above configured, start the crew from the
repo root with `python -m crew`. Pass a task string to run just that task.

The crew runs as one always-on replica in Container App
`ca-agent-crew` (see "Compute" above). On start it loads config
from the environment only, reads its token from Key Vault
`kv-agentcrew-vaxvu3c2`, and picks up its next task from GitHub
issues in this repo. Roles: Planner (GPT-6.2 Astra) plans,
Builder (GPT-6.2 Sol) implements and opens the PR, Reviewer
(GPT-6.2 Astra) reviews against Ponytail and signs off, Merger
(GPT-6.2 Sol) merges once tests, CI, and Reviewer sign-off are
green. Full operating instructions: `docs/crew-instructions.md`;
coding rules: `ponytail.md`, pointed at by `AGENTS.md`.

The first task is building/improving v1 of itself in this repo —
the crew package, the gates, the feedback loop, and the scope lock.
That is project #1. The next project after v1 is the ResumeBot
multi-tenant platform (a separate private repo), arriving through
an issue when v1 is ready.

## Shipping the crew image (GHCR flow)

The crew token stays Actions read-only — that is fine, because the
GHCR push never uses the crew token. The flow is:

1. The crew writes code and opens PRs in `warni001/agent-crew`.
2. CI merges (tests, Reviewer sign-off, CI green).
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
   digest. A deploy restarts the container, so an in-flight task
   restarts — finished work is never re-run (the "[crew] Done:"
   issue guard). The manual fallback, if the deploy job ever needs
   bypassing, is:

   ```bash
   az containerapp update -g rg-agent-crew -n ca-agent-crew --image ghcr.io/warni001/agent-crew:sha-<commit-sha> --revision-suffix <suffix>
   ```

The crew must not attempt a direct GHCR push with its token — a
direct `docker push` with the crew PAT will fail (no Packages
permission) and is forbidden regardless. After the first push, the
package must be made public so the Container App can pull it
anonymously.
