"""Test the /openai/v1 path with api-version=preview, no framework.

FROZEN HISTORICAL DIAGNOSTIC (Nov 19, 2026): this probe is
Magentic-era — it imports ORCHESTRATOR_PROGRESS_LEDGER_PROMPT from
agent_framework_orchestrations._magentic and is NOT runnable against
the current sequential code. Do not "fix" it to run; it is a record
of the probe that proved api-version=preview on the /openai/v1 path,
not a tool.

The framework client always uses the /openai/v1 base URL and its default
api-version is "preview". We overrode it with 2025-04-01-preview and Azure
400s that combo ("API version not supported"). This probe tests the
framework's exact combination before we change the code.

Run from the repo root:

    cd ~/Documents/GitHub/agent-crew && source .venv/bin/activate && python /tmp/v1-probe.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.environ.get("AGENT_CREW_REPO", os.getcwd()))

from azure.identity import DefaultAzureCredential
from agent_framework_orchestrations._magentic import ORCHESTRATOR_PROGRESS_LEDGER_PROMPT

V1_URL = "https://foundry-gpt-vaxvu3c2.openai.azure.com/openai/v1/responses?api-version=preview"

TASK = "Diagnose why the Magentic progress ledger fails to parse."
TEAM = (
    "- planner: plans the work\n"
    "- builder: writes code\n"
    "- reviewer: reviews code\n"
    "- merger: merges PRs"
)
NAMES = "planner, builder, reviewer, merger"


def get_token(scope: str) -> str | None:
    try:
        token = DefaultAzureCredential().get_token(scope).token
        print(f"token {scope}: ok")
        return token
    except Exception as exc:
        print(f"token {scope}: FAILED ({exc})")
        return None


def call(url: str, token: str, model: str, prompt: str) -> dict:
    body = json.dumps({"model": model, "input": prompt}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return {"http": resp.status, "body": json.loads(resp.read())}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw[:500].decode("utf-8", "replace")}
        return {"http": exc.code, "body": parsed}


def show(label: str, result: dict) -> None:
    print(f"===== {label} http={result['http']} =====")
    print(json.dumps(result["body"], indent=2))
    print()


def main() -> None:
    cog_token = get_token("https://cognitiveservices.azure.com/.default")
    ledger_prompt = ORCHESTRATOR_PROGRESS_LEDGER_PROMPT.format(task=TASK, team=TEAM, names=NAMES)
    hello = "Say hello in one sentence."
    if cog_token:
        show("v1 + preview + hello", call(V1_URL, cog_token, "gpt-5-4-mini", hello))
        show("v1 + preview + ledger", call(V1_URL, cog_token, "gpt-5-4-mini", ledger_prompt))


main()
