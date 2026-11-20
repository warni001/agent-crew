"""Repro for the Magentic progress-ledger parse failure (diagnostic scratch).

Sends the exact ORCHESTRATOR_PROGRESS_LEDGER_PROMPT to gpt-6-2-astra through the
same path the manager uses (Agent.run, fresh session, last message) and prints what
comes back: the last message's text verbatim, its content types, and whether it
contains a '{'.

Run from the repo root on the Mac (az login must be active):

    .venv/bin/python infra/ledger-repro.py [--structured]

Empty text -> the model returns reasoning-only / no message item (hypothesis 1).
Prose -> it answers in natural language despite the JSON instruction (hypothesis 2).
Valid JSON -> the live failure is history-dependent; rerun with the real history.

--structured sends the same prompt with the json_schema response_format
from crew/manager.py, the same fix the crew's manager uses. If the ledger
parses there, the fix is verified live before any deploy.
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.environ.get("AGENT_CREW_REPO", os.getcwd()))

from agent_framework import Agent, Message
from agent_framework_orchestrations._magentic import (
    ORCHESTRATOR_PROGRESS_LEDGER_PROMPT,
    _extract_json,
)

from crew.config import CrewConfig
from crew.manager import LEDGER_RESPONSE_FORMAT
from crew.roles import _MANAGER_INSTRUCTIONS, make_chat_client

ENDPOINT = os.environ.get(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://foundry-gpt-vaxvu3c2.services.ai.azure.com/api/projects/crew",
)

TASK = "Diagnose why the Magentic progress ledger fails to parse."
TEAM = (
    "- planner: plans the work\n"
    "- builder: writes code\n"
    "- reviewer: reviews code\n"
    "- merger: merges PRs"
)
NAMES = "planner, builder, reviewer, merger"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--structured", action="store_true")
    args = parser.parse_args()
    config = CrewConfig(foundry_project_endpoint=ENDPOINT)
    manager = Agent(
        make_chat_client(config, config.manager_model),
        instructions=_MANAGER_INSTRUCTIONS,
        name="manager",
    )
    prompt = ORCHESTRATOR_PROGRESS_LEDGER_PROMPT.format(task=TASK, team=TEAM, names=NAMES)
    session = manager.create_session()
    options = {"response_format": LEDGER_RESPONSE_FORMAT} if args.structured else None
    response = await manager.run([Message(role="user", contents=[prompt])], session=session, options=options)
    print(f"structured: {args.structured}")
    print(f"messages: {len(response.messages)}")
    last = response.messages[-1]
    print(f"last.text repr: {last.text!r}")
    print(f"last.text has '{{': {'{' in last.text}")
    print("last.contents:")
    for c in last.contents:
        print(f"  type={c.type} text={getattr(c, 'text', None)!r}")
    try:
        parsed = _extract_json(last.text)
    except Exception as ex:  # noqa: BLE001 - repro output, show the failure
        print(f"ledger parses: no ({ex})")
    else:
        print(f"ledger parses: yes, keys={sorted(parsed)}")


asyncio.run(main())
