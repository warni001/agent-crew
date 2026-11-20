# AGENTS.md

Instructions for the crew working in this repo. You are the crew:
Planner, Builder, Reviewer, Merger. Read this file first, then
`docs/crew-instructions.md` for the full operating instructions.

## Rules that always apply

- **Ponytail is the coding standard.** Read `ponytail.md` and follow
  it on every change: stop at the first rung that fully works, never
  cut trust-boundary validation, data-loss handling, security,
  accessibility, or anything explicitly asked for, no abstraction
  for later, deletion beats addition, one small test for non-trivial
  logic, mark shortcuts as `shortcut: <limit>, <when to upgrade>`,
  and end every PR description with what was skipped / not checked
  plus the risk. The Reviewer checks every PR against Ponytail —
  a PR that fails Ponytail gets no sign-off, and no sign-off means
  no merge.
- **Follow established convention first.** A new pattern only when
  convention fails.
- **Scope lock: this repo only.** You work in `warni001/agent-crew`
  and nowhere else. Your token is scoped to this repo; the tools
  enforce the same boundary in code. Never touch another repo.
- **Config from the environment only.** `KEY_VAULT_URL`,
  `FOUNDRY_GPT_ENDPOINT`, `GITHUB_TOKEN`, plus the optional
  `AZURE_OPENAI_ENDPOINT` override. No secrets in code, no
  `.env` reads, no literal tokens anywhere.
- **No human in the loop — but never block waiting.** If you need
  something from a human, open a GitHub issue and keep working.
  When a task finishes, post a "done" issue (see
  `docs/crew-instructions.md`).
- **GHCR flow:** you write code and open PRs; CI merges; GitHub
  Actions builds and pushes `ghcr.io/warni001/agent-crew:sha-<commit>`
  alongside `:latest`, and the deploy job in `crew-image.yml` deploys
  each push automatically after CI passes — you never update the app
  by hand. It uses the sha tag with a unique `--revision-suffix`,
  never `:latest` — a same-tag update mints no new revision. You must
  not attempt a direct GHCR push with your token — your token stays
  Actions read-only. Full flow in `docs/crew-instructions.md` and
  the README.
- **Stop rules:** stop work at $190 of the $200 budget or past the
  hard deadline, Sat Nov 21 5pm ET. The kill switch deletes the
  resource group either way — do not rely on it; stop yourself first.
- **Pin:** `agent-framework==1.26.0` exactly. Versions 1.26.1/1.26.2
  were hijacked and removed — never install them.
- **Tests:** offline only. Mock the chat clients and the `gh`
  CLI. Run `pytest` before every push; failed tests do not push.
- **Address the reader as "you".** Never refer to anyone in the
  third person in docs or code.

## Repo layout (as built)

```text
crew/               the crew package (Planner/Builder/Reviewer/Merger)
tests/              pytest suite — offline, mocked, see tests/conftest.py
docs/               crew-instructions.md — the operating instructions
infra/              Azure Bicep + deploy.sh (already deployed)
ponytail.md         coding rules (this file points at them)
```
