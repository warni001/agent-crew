"""Docs contract: the three documents carry the GHCR flow and Ponytail.

These tests run standalone — no ``crew/`` code needed — and must pass
now. They pin the two rules the repo owner set:

1. GHCR flow: crew writes code and opens PRs; CI merges; Actions
   builds and pushes ``ghcr.io/warni001/agent-crew:latest`` and a
   sha-tagged image with ``GITHUB_TOKEN`` (``packages: write``); the
   deploy job in ``crew-image.yml`` then updates the Container App to
   the sha-tagged image automatically once CI passes on the commit,
   with a unique revision suffix — never ``:latest`` (an unchanged
   string mints no new revision, and a ``:latest`` pull can resolve
   stale). The crew PAT stays Actions read-only and the crew must
   never attempt a direct GHCR push with it. The package must be made
   public after the first push so the Container App can pull
   anonymously.
2. Ponytail: the full rules live in ``ponytail.md``, ``AGENTS.md``
   points the crew at them, and the Reviewer checks PRs against them.

Also pinned: docs/code address the reader as "you", never in the
third person.
"""

from __future__ import annotations

import re

GHCR_IMAGE = "ghcr.io/warni001/agent-crew"
UPDATE_CMD = (
    "az containerapp update -g rg-agent-crew -n ca-agent-crew "
    "--image ghcr.io/warni001/agent-crew:sha-<commit-sha> "
    "--revision-suffix <suffix>"
)


def _read(repo_root, path):
    # Collapse whitespace so line-wrapped prose still matches phrases.
    return re.sub(r"\s+", " ", (repo_root / path).read_text())


def test_readme_has_how_to_run_locally(repo_root):
    text = _read(repo_root, "README.md").lower()
    assert "run locally" in text or "how to run" in text
    assert "pytest" in text


def test_readme_has_how_the_crew_starts(repo_root):
    text = _read(repo_root, "README.md").lower()
    assert "how the crew starts" in text or "crew starts" in text


def test_readme_states_first_task_is_building_itself(repo_root):
    text = _read(repo_root, "README.md")
    assert "first task" in text.lower()
    assert "itself" in text


def test_readme_has_ghcr_flow(repo_root):
    text = _read(repo_root, "README.md")
    assert GHCR_IMAGE in text
    assert UPDATE_CMD in text
    assert "packages: write" in text
    assert "GITHUB_TOKEN" in text


def test_readme_states_auto_deploy(repo_root):
    text = _read(repo_root, "README.md").lower()
    assert "deploys automatically" in text
    assert "deploy job" in text
    assert "waits for ci to pass" in text


def test_crew_instructions_states_auto_deploy(repo_root):
    text = _read(repo_root, "docs/crew-instructions.md").lower()
    assert "deploys automatically" in text
    assert "deploy job" in text
    assert "waits for ci to pass" in text


def test_readme_notes_package_must_be_public(repo_root):
    text = _read(repo_root, "README.md").lower()
    assert "public" in text and "first push" in text


def test_crew_instructions_has_ghcr_flow(repo_root):
    text = _read(repo_root, "docs/crew-instructions.md")
    assert GHCR_IMAGE in text
    assert UPDATE_CMD in text
    assert "packages: write" in text
    lower = text.lower()
    assert "must not attempt a direct ghcr push" in lower
    assert "actions read-only" in lower or "actions read" in lower


def test_crew_instructions_forbids_workflow_edits(repo_root):
    text = _read(repo_root, "docs/crew-instructions.md").lower()
    assert ".github/workflows" in text
    assert "human-only" in text
    assert "no workflows permission" in text
    assert "proposed" in text


def test_crew_instructions_has_feedback_rule(repo_root):
    text = _read(repo_root, "docs/crew-instructions.md").lower()
    assert "never blocks waiting" in text
    assert '"done"' in text or "done" in text
    assert "github issues" in text


def test_crew_instructions_has_roles_and_gates(repo_root):
    text = _read(repo_root, "docs/crew-instructions.md").lower()
    for role in ("planner", "builder", "reviewer", "merger"):
        assert role in text
    assert "reviewer sign-off" in text or "sign-off" in text


def test_ponytail_has_all_six_rungs(repo_root):
    text = _read(repo_root, "ponytail.md").lower()
    for rung in (
        "does it need to exist",
        "reuse the codebase",
        "stdlib",
        "installed dep",
        "one line",
        "minimum that works",
    ):
        assert rung in text, f"missing Ponytail rung: {rung}"


def test_ponytail_has_never_cut_rules(repo_root):
    text = _read(repo_root, "ponytail.md").lower()
    for item in (
        "trust-boundary validation",
        "data-loss",
        "security",
        "accessibility",
    ):
        assert item in text, f"missing never-cut item: {item}"


def test_ponytail_has_no_abstraction_for_later(repo_root):
    text = _read(repo_root, "ponytail.md").lower()
    assert "for later" in text
    assert "deletion beats addition" in text


def test_ponytail_has_shortcut_marking(repo_root):
    text = _read(repo_root, "ponytail.md")
    assert "shortcut:" in text
    assert "when to upgrade" in text.lower()


def test_ponytail_has_skipped_and_risk_rule(repo_root):
    text = _read(repo_root, "ponytail.md").lower()
    assert "skipped" in text
    assert "not checked" in text
    assert "risk" in text


def test_ponytail_has_convention_first(repo_root):
    text = _read(repo_root, "ponytail.md").lower()
    assert "established convention" in text
    assert "a new pattern only when convention fails" in text


def test_ponytail_has_one_small_test_rule(repo_root):
    text = _read(repo_root, "ponytail.md").lower()
    assert "one small test" in text


def test_agents_md_points_at_ponytail(repo_root):
    text = _read(repo_root, "AGENTS.md")
    assert "ponytail.md" in text
    lower = text.lower()
    assert "reviewer" in lower and "ponytail" in lower


def test_crew_instructions_points_at_ponytail(repo_root):
    text = _read(repo_root, "docs/crew-instructions.md")
    assert "ponytail.md" in text
    assert "Reviewer" in text


def test_crew_instructions_names_verified_sequential_api(repo_root):
    """Sequential entry points as verified against installed 1.26.0."""
    text = _read(repo_root, "docs/crew-instructions.md")
    assert "agent_framework.orchestrations" in text
    assert "SequentialBuilder" in text
    assert "chain_only_agent_responses" in text
    # The doc must state the negatives explicitly — no magentic module,
    # no ChatAgent — so nobody writes them from memory.
    lower = text.lower()
    assert "there is no `agent_framework.magentic` module" in lower
    assert "there is no `chatagent` in 1.26.0" in lower


def test_crew_instructions_states_classic_endpoint(repo_root):
    """The instructions name the classic endpoint and the proven API
    version — the Foundry project endpoint story is gone."""
    text = _read(repo_root, "docs/crew-instructions.md")
    assert "OpenAIChatClient" in text
    assert "azure_endpoint" in text
    assert 'api_version="preview"' in text
    assert "FoundryChatClient" not in text
    assert "UNVERIFIED" not in text


def test_docs_never_refer_to_john_in_third_person(repo_root):
    """Docs address the reader as "you" — never "John" as a third party."""
    for path in ("README.md", "AGENTS.md", "ponytail.md", "docs/crew-instructions.md"):
        text = _read(repo_root, path)
        assert not re.search(r"\bJohn\b", text), f"{path} refers to John by name"
