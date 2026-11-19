"""Agent-framework pin: requirements.txt pins agent-framework==1.26.0.

Versions 1.26.1/1.26.2 were hijacked and removed — never install
them. The pin must be exact (``==``), not a range, and no other
1.26.x line may appear anywhere in the file.

This test is blocked until the core scaffold merges ``requirements.txt``
(it is not in this worktree yet). It SKIPs — not fails — while the
file is absent, so the suite stays green standalone and the gap is
visible in the skip list rather than hidden.
"""

from __future__ import annotations

import re

import pytest


def test_requirements_pins_agent_framework_1_26_0(repo_root):
    req_file = repo_root / "requirements.txt"
    if not req_file.exists():
        pytest.skip("blocked: requirements.txt not merged yet (core scaffold)")
    text = req_file.read_text()
    assert re.search(r"(?m)^agent-framework==1\.26\.0\s*$", text), (
        "requirements.txt must contain exactly 'agent-framework==1.26.0'"
    )


def test_no_hijacked_versions_in_requirements(repo_root):
    req_file = repo_root / "requirements.txt"
    if not req_file.exists():
        pytest.skip("blocked: requirements.txt not merged yet (core scaffold)")
    text = req_file.read_text()
    assert "1.26.1" not in text, "agent-framework 1.26.1 was hijacked — never install it"
    assert "1.26.2" not in text, "agent-framework 1.26.2 was hijacked — never install it"


def test_pin_is_exact_not_a_range(repo_root):
    req_file = repo_root / "requirements.txt"
    if not req_file.exists():
        pytest.skip("blocked: requirements.txt not merged yet (core scaffold)")
    text = req_file.read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("agent-framework"):
            assert stripped == "agent-framework==1.26.0", (
                f"pin must be exact '==', got: {stripped!r}"
            )


def test_readme_states_the_pin(repo_root):
    """The README documents the pin even before requirements.txt lands."""
    text = (repo_root / "README.md").read_text()
    assert "agent-framework==1.26.0" in text
    assert "1.26.1" in text and "1.26.2" in text  # the hijack warning
