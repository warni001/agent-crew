"""Config for the crew. Env only — no secrets, model IDs, or environment
values hardcoded here beyond env-overridable defaults.

Secrets: GITHUB_TOKEN is set at runtime from Key Vault (never in code,
files, or logs). Never edit a .env file directly — hand the exact line
to the operator instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ALLOWED_REPO = "warni001/agent-crew"


class ConfigError(ValueError):
    """Raised when required config is missing or empty."""

# FoundryChatClient (agent-framework 1.26.0, verified against the installed
# package) builds an AIProjectClient from the project endpoint, so the
# endpoint must be a Foundry *project* endpoint:
#   https://<account>.services.ai.azure.com/api/projects/<project>
# The Cognitive Services account endpoint
# (https://<account>.cognitiveservices.azure.com/) is NOT a project
# endpoint and will not work here. FOUNDRY_GPT_ENDPOINT is accepted as an
# alias for the project endpoint only; a cognitiveservices.azure.com value
# is rejected with a clear error. Untested against live Foundry.
_PROJECT_ENDPOINT_MARKER = "services.ai.azure.com/api/projects/"


@dataclass(frozen=True)
class CrewConfig:
    """Everything the crew needs, read from the environment."""

    repo: str = ALLOWED_REPO
    foundry_project_endpoint: str = ""
    key_vault_url: str = ""
    github_token: str = ""  # From Key Vault at runtime; never logged.
    planner_model: str = "gpt-6-2-astra"  # Deployment name, not model name.
    reviewer_model: str = "gpt-6-2-astra"
    builder_model: str = "gpt-6-2-sol"
    merger_model: str = "gpt-6-2-sol"
    manager_model: str = "gpt-6-2-astra"
    budget_limit_usd: float = 200.0
    budget_spent_usd: float = 0.0
    # Hard deadline: Sat Nov 21 5pm ET == 22:00 UTC.
    deadline_iso: str = "2026-11-21T22:00:00Z"
    ledger_path: str = "crew-ledger.jsonl"
    # Append-blob URL for the run ledger (bicep sets CREW_LEDGER_BLOB_URL
    # on the container app). Empty means the local file — the blob is
    # persistence, not a requirement.
    ledger_blob_url: str = ""
    max_rounds: int = 10

    @property
    def foundry_gpt_endpoint(self) -> str:
        """Alias for foundry_project_endpoint (the env var name)."""
        return self.foundry_project_endpoint

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> CrewConfig:
        """Build config from the environment (or a supplied mapping)."""
        env = os.environ if env is None else env
        endpoint = env.get("FOUNDRY_PROJECT_ENDPOINT") or env.get(
            "FOUNDRY_GPT_ENDPOINT", ""
        )
        return cls(
            repo=env.get("CREW_REPO", ALLOWED_REPO),
            foundry_project_endpoint=endpoint,
            key_vault_url=env.get("KEY_VAULT_URL", ""),
            github_token=env.get("GITHUB_TOKEN", ""),
            planner_model=env.get("CREW_MODEL_PLANNER", "gpt-6-2-astra"),
            reviewer_model=env.get("CREW_MODEL_REVIEWER", "gpt-6-2-astra"),
            builder_model=env.get("CREW_MODEL_BUILDER", "gpt-6-2-sol"),
            merger_model=env.get("CREW_MODEL_MERGER", "gpt-6-2-sol"),
            manager_model=env.get("CREW_MODEL_MANAGER", "gpt-6-2-astra"),
            budget_limit_usd=float(env.get("CREW_BUDGET_LIMIT_USD", "200")),
            budget_spent_usd=float(env.get("CREW_BUDGET_SPENT_USD", "0")),
            deadline_iso=env.get("CREW_DEADLINE", "2026-11-21T22:00:00Z"),
            ledger_path=env.get("CREW_LEDGER_PATH", "crew-ledger.jsonl"),
            ledger_blob_url=env.get("CREW_LEDGER_BLOB_URL", ""),
            max_rounds=int(env.get("CREW_MAX_ROUNDS", "10")),
        )

    def validate(self) -> None:
        """Raise ValueError on config that cannot run a task.

        Does not check the GitHub token: local runs that only read or plan
        may not need it, and the tools fail clearly when it is missing.
        """
        problems: list[str] = []
        if self.repo != ALLOWED_REPO:
            problems.append(
                f"CREW_REPO must be {ALLOWED_REPO}, got {self.repo!r}"
            )
        if not self.foundry_project_endpoint:
            problems.append(
                "set FOUNDRY_PROJECT_ENDPOINT (or FOUNDRY_GPT_ENDPOINT)"
            )
        elif _PROJECT_ENDPOINT_MARKER not in self.foundry_project_endpoint:
            problems.append(
                "FOUNDRY_PROJECT_ENDPOINT must be a Foundry project "
                "endpoint (...services.ai.azure.com/api/projects/<project>); "
                "a cognitiveservices.azure.com account endpoint will not work"
            )
        if self.budget_limit_usd <= 0:
            problems.append("CREW_BUDGET_LIMIT_USD must be > 0")
        if problems:
            raise ValueError("; ".join(problems))


# Short name used by docs and tests.
Config = CrewConfig


def load_config(env: dict[str, str] | None = None) -> CrewConfig:
    """Load the three required vars from the environment.

    Raises ConfigError naming the variable when one is missing or empty.
    Does NOT read .env files and does NOT run validate(): the endpoint
    shape check lives in validate() for actual runs.
    """
    env = os.environ if env is None else env
    values: dict[str, str] = {}
    for var in ("KEY_VAULT_URL", "FOUNDRY_GPT_ENDPOINT", "GITHUB_TOKEN"):
        value = env.get(var, "")
        if not value:
            raise ConfigError(f"missing required config: {var}")
        values[var] = value
    return CrewConfig(
        key_vault_url=values["KEY_VAULT_URL"],
        foundry_project_endpoint=values["FOUNDRY_GPT_ENDPOINT"],
        github_token=values["GITHUB_TOKEN"],
    )
