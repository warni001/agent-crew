"""Tools scope lock: the crew may only touch the agent-crew repo.

Contract under test (see docs/crew-instructions.md, "Module contract")::

    crew.tools.ALLOWED_REPOS == frozenset({"warni001/agent-crew"})
    crew.tools.GHCR_IMAGE == "ghcr.io/warni001/agent-crew:latest"
    crew.tools.assert_repo_allowed(repo)  # raises ValueError otherwise

The crew PAT is scoped to ``agent-crew`` only, and the tools must
enforce the same boundary in code: every ``gh`` call and every repo
argument is checked before anything runs. A scope slip is a stop,
not a warning. The GHCR push is deliberately NOT in this module —
it happens in GitHub Actions (``GITHUB_TOKEN``, ``packages: write``),
never via a direct docker push with the crew PAT.

Also covered here (standalone, runs now): the scope lock is stated
in docs/crew-instructions.md, so the rule survives even before the
code lands.

Blocked-until-merge parts skip until ``crew/tools.py`` exists.
"""

from __future__ import annotations

import pytest

from conftest import require_crew


@pytest.fixture
def tools_mod():
    return require_crew("tools")


def test_allowed_repos_is_agent_crew_only(tools_mod):
    assert tools_mod.ALLOWED_REPOS == frozenset({"warni001/agent-crew"})


def test_no_agent_toolset_has_check_credit(tools_mod):
    """Credit is enforced in code by the loop, never by the agents:
    the check_credit tool looped forever (each check re-sent full
    history as prompt tokens) and repeated checks preceded a 400
    about a missing tool call. No role may receive it again."""
    for name in ("READ_TOOLS", "BUILD_TOOLS", "REVIEW_TOOLS", "MERGE_TOOLS", "ALL_TOOLS"):
        tools = getattr(tools_mod, name)
        assert "check_credit" not in [t.name for t in tools], name
    assert not hasattr(tools_mod, "check_credit")


def test_ghcr_image_constant(tools_mod):
    assert tools_mod.GHCR_IMAGE == "ghcr.io/warni001/agent-crew:latest"


def test_image_for_commit_is_sha_tagged(tools_mod):
    assert tools_mod.image_for_commit("abc123") == (
        "ghcr.io/warni001/agent-crew:sha-abc123"
    )


def test_image_for_commit_rejects_empty_sha(tools_mod):
    with pytest.raises(ValueError):
        tools_mod.image_for_commit("  ")


@pytest.mark.parametrize("repo", ["warni001/agent-crew"])
def test_allowed_repo_passes(tools_mod, repo):
    tools_mod.assert_repo_allowed(repo)  # must not raise


@pytest.mark.parametrize(
    "repo",
    [
        "warni001/resume-bot",
        "warni001/teams-bot-framework",
        "other-org/agent-crew",
        "agent-crew",
        "",
        "warni001/agent-crew-extra",
    ],
)
def test_other_repos_rejected(tools_mod, repo):
    with pytest.raises(ValueError):
        tools_mod.assert_repo_allowed(repo)


def test_scope_lock_stated_in_crew_instructions(repo_root):
    text = (repo_root / "docs" / "crew-instructions.md").read_text()
    assert "warni001/agent-crew" in text
    assert "agent-crew only" in text.lower() or "only" in text.lower()
    assert "scope lock" in text.lower()


def test_no_direct_ghcr_push_in_crew_instructions(repo_root):
    """Docs must forbid a direct GHCR push with the crew PAT."""
    text = (repo_root / "docs" / "crew-instructions.md").read_text().lower()
    assert "must not attempt a direct ghcr push" in text
    assert "packages: write" in text
