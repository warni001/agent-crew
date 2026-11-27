"""Config for the crew. Env only — no secrets, model IDs, or environment
values hardcoded here beyond env-overridable defaults.

Secrets: GITHUB_TOKEN is set at runtime from Key Vault (never in code,
files, or logs). Never edit a .env file directly — hand the exact line
to the operator instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

ALLOWED_REPO = "warni001/agent-crew"


class ConfigError(ValueError):
    """Raised when required config is missing or empty."""

# Chat calls go to the classic Azure OpenAI endpoint
# (https://<account>.openai.azure.com), NOT the Foundry project
# endpoint — proven live Nov 19: the project endpoint returns HTTP
# 200 with zero usage and no message output even for "Say hello",
# while the classic endpoint returns real text and the ledger prompt
# returns valid JSON with no format forcing. OpenAIChatClient
# (agent-framework-openai 1.18.2) takes the classic endpoint as
# azure_endpoint with api_version "preview" (see
# crew/clients.py). FOUNDRY_PROJECT_ENDPOINT is still set by bicep;
# the classic endpoint is derived from it when AZURE_OPENAI_ENDPOINT
# is unset, so no bicep change is needed.
_CLASSIC_ENDPOINT_SUFFIX = ".openai.azure.com"


def derive_classic_endpoint(value: str) -> str:
    """Classic Azure OpenAI endpoint for a Foundry account value.

    ``https://<account>.services.ai.azure.com/api/projects/<project>``
    and ``https://<account>.cognitiveservices.azure.com/`` both name
    the same account, whose classic endpoint is
    ``https://<account>.openai.azure.com``. A classic endpoint passes
    through unchanged. Anything else returns "" (not derivable).
    """
    host = urlparse(value or "").hostname or ""
    for suffix in (".services.ai.azure.com", ".cognitiveservices.azure.com"):
        if host.endswith(suffix):
            return f"https://{host[: -len(suffix)]}{_CLASSIC_ENDPOINT_SUFFIX}"
    if host.endswith(_CLASSIC_ENDPOINT_SUFFIX):
        return f"https://{host}"
    return ""


@dataclass(frozen=True)
class CrewConfig:
    """Everything the crew needs, read from the environment."""

    repo: str = ALLOWED_REPO
    foundry_project_endpoint: str = ""
    # Explicit classic endpoint override (env AZURE_OPENAI_ENDPOINT).
    # Empty means "derive it from foundry_project_endpoint".
    azure_openai_endpoint: str = ""
    key_vault_url: str = ""
    github_token: str = ""  # From Key Vault at runtime; never logged.
    planner_model: str = "gpt-5-4-mini"  # Deployment name, not model name.
    reviewer_model: str = "gpt-5-4-mini"
    builder_model: str = "gpt-5-4-mini"
    merger_model: str = "gpt-5-4-mini"
    manager_model: str = "gpt-5-4-mini"  # Unused under sequential
    # orchestration (there is no manager agent); retained so the env
    # knob stays valid if a coordinating agent is added back.
    budget_limit_usd: float = 200.0
    # Manual starting offset: spend incurred before the tracker existed.
    # It only seeds the tracker at process start (crew/spend.py); the
    # live total is what the tracker counts from API-reported usage.
    budget_spent_usd: float = 0.0
    # Hard deadline: Sat Dec 26 3:08:36pm ET == 20:08:36 UTC.
    deadline_iso: str = "2026-12-26T20:08:36Z"
    ledger_path: str = "crew-ledger.jsonl"
    # Append-blob URL for the run ledger (bicep sets CREW_LEDGER_BLOB_URL
    # on the container app). Empty means the local file — the blob is
    # persistence, not a requirement.
    ledger_blob_url: str = ""
    # Round cap for one Magentic run. Unused under sequential
    # orchestration (there are no rounds, only one pass per agent);
    # retained so CREW_MAX_ROUNDS stays a valid knob if a loop-back
    # is added later. The framework default is None (unlimited); the
    # cap existed for stall detection plus bounded burn: a stuck task
    # must stop and be re-queued by a human instead of burning the
    # whole $200 budget. 10 was an arbitrary scaffold default and too
    # small: the first self-build hit it and terminated with zero PRs.
    # 30 fits real tasks; the $190 credit guard and the Saturday
    # deadline still cap total spend.
    max_rounds: int = 30

    @property
    def foundry_gpt_endpoint(self) -> str:
        """Alias for foundry_project_endpoint (the env var name)."""
        return self.foundry_project_endpoint

    def resolve_azure_openai_endpoint(self) -> str:
        """Classic endpoint for chat calls: the explicit
        AZURE_OPENAI_ENDPOINT wins, else derive it from the Foundry
        project/account endpoint. "" means nothing usable was set."""
        if self.azure_openai_endpoint:
            return self.azure_openai_endpoint.rstrip("/")
        return derive_classic_endpoint(self.foundry_project_endpoint)

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
            azure_openai_endpoint=env.get("AZURE_OPENAI_ENDPOINT", ""),
            key_vault_url=env.get("KEY_VAULT_URL", ""),
            github_token=env.get("GITHUB_TOKEN", ""),
            planner_model=env.get("CREW_MODEL_PLANNER", "gpt-5-4-mini"),
            reviewer_model=env.get("CREW_MODEL_REVIEWER", "gpt-5-4-mini"),
            builder_model=env.get("CREW_MODEL_BUILDER", "gpt-5-4-mini"),
            merger_model=env.get("CREW_MODEL_MERGER", "gpt-5-4-mini"),
            manager_model=env.get("CREW_MODEL_MANAGER", "gpt-5-4-mini"),
            budget_limit_usd=float(env.get("CREW_BUDGET_LIMIT_USD", "200")),
            budget_spent_usd=float(env.get("CREW_BUDGET_SPENT_USD", "0")),
            deadline_iso=env.get("CREW_DEADLINE", "2026-12-26T20:08:36Z"),
            ledger_path=env.get("CREW_LEDGER_PATH", "crew-ledger.jsonl"),
            ledger_blob_url=env.get("CREW_LEDGER_BLOB_URL", ""),
            max_rounds=int(env.get("CREW_MAX_ROUNDS", "30")),
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
        endpoint = self.resolve_azure_openai_endpoint()
        if not endpoint:
            problems.append(
                "no usable chat endpoint: set AZURE_OPENAI_ENDPOINT, or "
                "FOUNDRY_PROJECT_ENDPOINT as a Foundry project or account endpoint"
            )
        elif not (urlparse(endpoint).hostname or "").endswith(
            _CLASSIC_ENDPOINT_SUFFIX
        ):
            problems.append(
                "AZURE_OPENAI_ENDPOINT must be a classic Azure OpenAI "
                f"endpoint (https://<account>.openai.azure.com), got {endpoint!r}"
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
        azure_openai_endpoint=env.get("AZURE_OPENAI_ENDPOINT", ""),
        github_token=values["GITHUB_TOKEN"],
    )
