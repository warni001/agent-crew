"""Ledger reviewer sign-off gate: no merge without Reviewer sign-off.

Contract under test (see docs/crew-instructions.md, "Module contract")::

    crew.gates.REVIEWER_ROLE == "reviewer"
    crew.gates.can_merge(reviews) -> bool

``reviews`` is an iterable of mappings with ``role`` and ``approved``
keys (e.g. ``{"role": "reviewer", "approved": True}``). Merge is
allowed only when at least one review has role ``"reviewer"`` and
``approved`` true. Builder self-approval, Planner approval, or a
Reviewer review that is not an approval must never open the gate —
that is the whole point of the gate, since there is no human review
in the loop.

Blocked until ``crew/gates.py`` is merged; these tests skip until then.
"""

from __future__ import annotations

import pytest

from conftest import require_crew


@pytest.fixture
def gates_mod():
    return require_crew("gates")


def test_reviewer_role_constant(gates_mod):
    assert gates_mod.REVIEWER_ROLE == "reviewer"


def test_reviewer_approval_opens_gate(gates_mod):
    assert gates_mod.can_merge([{"role": "reviewer", "approved": True}]) is True


def test_builder_approval_alone_does_not_open_gate(gates_mod):
    assert gates_mod.can_merge([{"role": "builder", "approved": True}]) is False


def test_planner_approval_alone_does_not_open_gate(gates_mod):
    assert gates_mod.can_merge([{"role": "planner", "approved": True}]) is False


def test_reviewer_rejection_keeps_gate_closed(gates_mod):
    assert gates_mod.can_merge([{"role": "reviewer", "approved": False}]) is False


def test_no_reviews_keeps_gate_closed(gates_mod):
    assert gates_mod.can_merge([]) is False


def test_mixed_reviews_need_reviewer_approval(gates_mod):
    reviews = [
        {"role": "builder", "approved": True},
        {"role": "planner", "approved": True},
        {"role": "reviewer", "approved": False},
    ]
    assert gates_mod.can_merge(reviews) is False
    reviews[2]["approved"] = True
    assert gates_mod.can_merge(reviews) is True


def test_merger_role_alone_does_not_open_gate(gates_mod):
    assert gates_mod.can_merge([{"role": "merger", "approved": True}]) is False


def test_review_missing_approved_key_is_not_approval(gates_mod):
    assert gates_mod.can_merge([{"role": "reviewer"}]) is False
