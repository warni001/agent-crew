"""Branch check: the builder must reuse a branch that already holds a
change instead of creating a duplicate, and the merger must merge an
approved PR even when sibling PRs in the same run have
changes_requested.

Regression (Nov 20, 2026): a run on task #10 rebuilt three changes on
fresh branches (round4-gate-latest, round4-pr-result, round4-local-tests)
and opened PRs #34/#35/#36 from them, although the same changes already
existed on round4-merge-signoff, round4-report-opened-pr, and
round4-readme-status. Nothing told the builder to check for an existing
branch first. In that same run the reviewer approved #36 while requesting
changes on #34/#35, and the merger — after the --merge fix — must merge
#36 on its own sign-off, not wait on its siblings.
"""

from __future__ import annotations

from unittest import mock

from conftest import require_crew


def _fake_run(monkeypatch, calls, stdout=""):
    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        result = mock.Mock()
        result.returncode = 0
        result.stdout = stdout
        result.stderr = ""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)


def test_planner_names_existing_branch_or_states_none():
    """The planner checks for a branch that already holds each change
    and names it (and any open PR) in the plan, or states none exists."""
    roles_mod = require_crew("roles")
    text = roles_mod._PLANNER_INSTRUCTIONS
    assert "list_branches" in text
    assert "list_open_prs" in text
    assert "name the branch" in text
    assert "if none" in text.lower() and "say so" in text
    assert "already merged or covered by an open PR" in text


def test_builder_checks_existing_branch_before_creating_one():
    """The builder checks for an existing branch holding the change,
    verifies base/diff/open-PR state, and opens the PR from that branch
    instead of creating a duplicate; a missing, stale, or merged branch
    falls back to a fresh branch."""
    import re

    roles_mod = require_crew("roles")
    # Collapse whitespace so line-wrapped prose still matches phrases.
    text = re.sub(r"\s+", " ", roles_mod._BUILDER_INSTRUCTIONS)
    assert "Before creating a branch" in text
    assert "list_branches" in text
    assert "git diff main...<branch>" in text
    assert "based on current main" in text
    assert "not already merged" in text
    assert "no equivalent PR" in text and "list_open_prs" in text
    assert "open the PR from that branch" in text
    assert "missing, stale, or already merged" in text


def test_merger_judges_each_pr_on_its_own_sign_off():
    """A changes_requested verdict on another PR never blocks merging
    an approved one — the #36 case."""
    roles_mod = require_crew("roles")
    text = roles_mod._MERGER_INSTRUCTIONS
    assert "Judge each PR on its own sign-off" in text
    assert "never blocks merging this one" in text


def test_list_branches_calls_gh_api(monkeypatch):
    tools_mod = require_crew("tools")
    calls: list[list[str]] = []
    _fake_run(monkeypatch, calls, "main\nround4-merge-signoff\n")
    out = tools_mod.list_branches("warni001/agent-crew")
    assert calls == [
        ["gh", "api", "repos/warni001/agent-crew/branches", "--paginate", "--jq", ".[].name"]
    ]
    assert "round4-merge-signoff" in out


def test_list_open_prs_calls_gh_pr_list(monkeypatch):
    tools_mod = require_crew("tools")
    calls: list[list[str]] = []
    _fake_run(monkeypatch, calls, "[]")
    tools_mod.list_open_prs("warni001/agent-crew")
    assert calls == [
        [
            "gh", "pr", "list", "--repo", "warni001/agent-crew",
            "--state", "open", "--json", "number,title,headRefName",
        ]
    ]


def test_list_open_prs_filters_by_head_branch(monkeypatch):
    tools_mod = require_crew("tools")
    calls: list[list[str]] = []
    _fake_run(monkeypatch, calls, "[]")
    tools_mod.list_open_prs("warni001/agent-crew", head="round4-merge-signoff")
    assert "--head" in calls[0] and "round4-merge-signoff" in calls[0]


def test_listing_tools_are_in_planner_and_builder_toolsets():
    """The planner and the builder both get the listing tools; the
    listing is how either of them can check for an existing branch or
    open PR at all (gh is not in run_shell's allowlist)."""
    tools_mod = require_crew("tools")
    for name in ("READ_TOOLS", "BUILD_TOOLS", "ALL_TOOLS"):
        names = [t.name for t in getattr(tools_mod, name)]
        assert "list_branches" in names, name
        assert "list_open_prs" in names, name


def test_approved_pr_merges_despite_sibling_changes_requested(monkeypatch, tmp_path):
    """The #36 case, at the tool level: changes_requested on #34/#35
    does not change the verdict for #36, so the merger's per-PR check
    returns YES for it and NO for the siblings. The merge itself goes
    through merge_pr with --merge (pinned by test_merge_pr.py)."""
    tools_mod = require_crew("tools")
    monkeypatch.setenv("CREW_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    tools_mod.record_sign_off(34, "changes_requested", "gate hole")
    tools_mod.record_sign_off(35, "changes_requested", "error URL")
    tools_mod.record_sign_off(36, "approved", "docs-only, tests pass")
    assert tools_mod.check_review_sign_off(36).startswith("YES")
    assert tools_mod.check_review_sign_off(34).startswith("NO")
    assert tools_mod.check_review_sign_off(35).startswith("NO")
