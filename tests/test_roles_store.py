"""Every role agent runs with store=False (no Responses chaining).

Magentic shares each participant's tool transcript with every other
participant, but each agent owns a separate Responses chain — under
chaining the client strips the foreign function_call yet still emits
its function_result, and Azure 400s. Regression test for the Nov 19
evening 400s that killed the #7, #8, and #9 runs.
"""

from crew.config import CrewConfig
from crew.roles import build_roles

PROJECT_ENDPOINT = "https://foundry-gpt-vaxvu3c2.cognitiveservices.azure.com/"


def test_all_roles_disable_server_side_store() -> None:
    roles = build_roles(CrewConfig(foundry_project_endpoint=PROJECT_ENDPOINT))
    assert set(roles) == {"planner", "builder", "reviewer", "merger", "manager"}
    for name, agent in roles.items():
        assert agent.default_options.get("store") is False, name
