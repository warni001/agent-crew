"""Plain Magentic manager: the progress ledger is a plain call, no format.

Contract (see crew/orchestration.py): the manager is the plain
StandardMagenticManager — no StructuredLedgerManager, no
response_format. The ledger parses because the model returns real
text on the classic Azure OpenAI endpoint (proven live Nov 19: the
exact ledger prompt returns valid JSON with no format forcing; the
Foundry project endpoint returned hollow 200s, which is what broke
the ledger). All model calls are faked; no network.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from agent_framework import Message
from agent_framework.orchestrations import MagenticContext, StandardMagenticManager

from conftest import require_crew


@pytest.fixture
def orchestration_mod():
    return require_crew("orchestration")


@pytest.fixture
def config_mod():
    return require_crew("config")


VALID_LEDGER = {
    "is_request_satisfied": {"reason": "work remains", "answer": False},
    "is_in_loop": {"reason": "first round", "answer": False},
    "is_progress_being_made": {"reason": "just starting", "answer": True},
    "next_speaker": {"reason": "plan first", "answer": "planner"},
    "instruction_or_question": {"reason": "needs a plan", "answer": "Plan the task."},
}


def make_context():
    return MagenticContext(
        task="Do the thing.",
        participant_descriptions={
            "planner": "plans",
            "builder": "builds",
            "reviewer": "reviews",
            "merger": "merges",
        },
    )


class FakeAgent:
    """Stand-in for Agent: returns canned messages, records run kwargs."""

    def __init__(self, replies, name="manager"):
        self.replies = list(replies)
        self.calls = []
        self.id = f"fake-{name}"
        self.name = name
        self.description = f"fake {name} agent"

    def create_session(self):
        return None

    def get_session(self, *args, **kwargs):
        return None

    async def run(self, messages, *, session=None, options=None, **kwargs):
        self.calls.append({"messages": list(messages), "options": options})
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return SimpleNamespace(messages=[self.replies[index]])


def _run(manager, context):
    return asyncio.run(manager.create_progress_ledger(context))


def _msg(text):
    return Message(role="assistant", contents=[text])


def _empty_msg():
    return Message(role="assistant", contents=[])


def test_plain_ledger_call_parses_valid_json():
    manager = StandardMagenticManager(FakeAgent([_msg(json.dumps(VALID_LEDGER))]))
    ledger = _run(manager, make_context())
    assert ledger.is_request_satisfied.answer is False
    assert ledger.is_in_loop.answer is False
    assert ledger.is_progress_being_made.answer is True
    assert ledger.next_speaker.answer == "planner"
    assert ledger.instruction_or_question.answer == "Plan the task."


def test_plain_ledger_call_passes_no_response_format():
    """The ledger call is plain: no options, no format forcing."""
    agent = FakeAgent([_msg(json.dumps(VALID_LEDGER))])
    manager = StandardMagenticManager(agent)
    _run(manager, make_context())
    assert len(agent.calls) == 1
    assert agent.calls[0]["options"] is None


def test_empty_response_still_fails_after_retries():
    """An empty reply still fails closed after the parent's retries —
    the fix was the endpoint returning text, not the manager."""
    agent = FakeAgent([_empty_msg()])
    manager = StandardMagenticManager(agent)
    with pytest.raises(RuntimeError, match="Progress ledger parse failed after 3 attempt"):
        _run(manager, make_context())
    assert len(agent.calls) == 3


def test_wiring_builds_workflow_with_plain_manager(orchestration_mod, config_mod, monkeypatch):
    """build_workflow passes the plain manager agent, not a subclass."""
    def fake_roles(config):
        roles = {name: FakeAgent([], name=name) for name in ("planner", "builder", "reviewer", "merger")}
        roles["manager"] = FakeAgent([_msg(json.dumps(VALID_LEDGER))])
        return roles

    monkeypatch.setattr(orchestration_mod, "build_roles", fake_roles)
    env = {
        "FOUNDRY_PROJECT_ENDPOINT": "https://foundry-gpt-vaxvu3c2.services.ai.azure.com/api/projects/crew",
        "KEY_VAULT_URL": "https://kv-example.vault.azure.net/",
        "GITHUB_TOKEN": "token",
    }
    config = config_mod.CrewConfig.from_env(env=env)
    workflow = orchestration_mod.build_workflow(config)
    assert workflow is not None
