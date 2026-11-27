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
    import re

    # Collapse whitespace so line-wrapped prose still matches phrases.
    text = re.sub(r"\s+", " ", roles_mod._PLANNER_INSTRUCTIONS)
    assert "list_branches" in text
    assert "list_open_prs" in text
    assert "name the branch" in text
    assert "if none" in text.lower() and "say so" in text
    assert "already merged or covered by an open PR" in text
    # Regression (Nov 27, #75 retry): the planner hedged a file path
    # ("possibly tests/test_main.py", "I did not find ...") and the
    # builder seized on the hedge to refuse the whole plan. A plan
    # names only paths the planner verified exist.
    assert "Name only file paths you verified exist" in text
    assert "never write 'possibly" in text
    assert "leave it out" in text


def test_builder_is_told_the_branch_is_already_created():
    """Regression (Nov 27, 2026): the #75 runs stalled because the
    builder refused to create a branch — 'I have not yet been asked
    to create or update one' — even though the planner had said no
    existing branch covers the change. Branching is now deterministic
    code, not a builder decision: run_one_task creates and checks out
    crew/task-<n> before the workflow runs, and the builder is told
    the branch is already there. It never creates one."""
    import re

    roles_mod = require_crew("roles")
    # Collapse whitespace so line-wrapped prose still matches phrases.
    text = re.sub(r"\s+", " ", roles_mod._BUILDER_INSTRUCTIONS)
    assert "already on the task branch" in text
    assert "already created and checked out for this task" in text
    assert "git branch --show-current" in text
    assert "Never create a branch" in text
    assert "never implement on main" in text
    assert "continue that work instead of starting over" in text


def test_builder_must_call_a_tool_before_refusing():
    """Regression (Nov 27, 2026): the #75 retry stalled because the
    builder refused in prose — 'no verified task plan' — while making
    zero tool calls. A conclusion about the plan needs evidence first:
    the builder reads the files the plan names, and a file it cannot
    find is a note, not a stop."""
    import re

    roles_mod = require_crew("roles")
    text = re.sub(r"\s+", " ", roles_mod._BUILDER_INSTRUCTIONS)
    assert "Before concluding anything about the plan" in text
    assert "read the files it names" in text
    assert "not a reason to stop" in text
    assert "without first calling at least one tool" in text
    assert "Use the exact file paths the planner named" in text


def _scripted_run(monkeypatch, calls, script):
    """Fake subprocess.run: script maps an exact command list to
    (returncode, stdout); anything unlisted succeeds with no output."""

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        rc, out = script.get(tuple(cmd), (0, ""))
        result = mock.Mock()
        result.returncode = rc
        result.stdout = out
        result.stderr = ""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)


def test_task_branch_name_from_issue_and_text():
    """Issue tasks get crew/task-<n>; anything else gets a stable
    hash of the task text, so a re-queue reuses its branch."""
    import hashlib

    tools_mod = require_crew("tools")
    assert tools_mod.task_branch_name(75, "do the thing") == "crew/task-75"
    hashed = tools_mod.task_branch_name(None, "do the thing")
    assert hashed == "crew/task-" + hashlib.sha1(b"do the thing").hexdigest()[:8]
    assert tools_mod.task_branch_name(None, "do the thing") == hashed


def test_ensure_task_branch_creates_from_main_when_missing(monkeypatch):
    tools_mod = require_crew("tools")
    calls: list[list[str]] = []
    _scripted_run(monkeypatch, calls, {
        ("git", "show-ref", "--verify", "refs/heads/crew/task-75"): (1, ""),
        ("git", "ls-remote", "--heads", "origin", "crew/task-75"): (0, ""),
    })
    assert tools_mod.ensure_task_branch("crew/task-75") == "crew/task-75"
    assert calls == [
        ["git", "fetch", "origin", "main"],
        ["git", "show-ref", "--verify", "refs/heads/crew/task-75"],
        ["git", "ls-remote", "--heads", "origin", "crew/task-75"],
        ["git", "checkout", "-b", "crew/task-75", "origin/main"],
    ]


def test_ensure_task_branch_checks_out_existing_local_branch(monkeypatch):
    """The Nov 20 duplicate-branch regression, at the code level: an
    existing branch is checked out, not rebuilt on a fresh one."""
    tools_mod = require_crew("tools")
    calls: list[list[str]] = []
    _scripted_run(monkeypatch, calls, {})
    assert tools_mod.ensure_task_branch("crew/task-75") == "crew/task-75"
    assert calls == [
        ["git", "fetch", "origin", "main"],
        ["git", "show-ref", "--verify", "refs/heads/crew/task-75"],
        ["git", "checkout", "crew/task-75"],
    ]


def test_ensure_task_branch_tracks_remote_only_branch(monkeypatch):
    tools_mod = require_crew("tools")
    calls: list[list[str]] = []
    _scripted_run(monkeypatch, calls, {
        ("git", "show-ref", "--verify", "refs/heads/crew/task-75"): (1, ""),
        ("git", "ls-remote", "--heads", "origin", "crew/task-75"): (
            0, "abc123\trefs/heads/crew/task-75\n",
        ),
    })
    assert tools_mod.ensure_task_branch("crew/task-75") == "crew/task-75"
    assert [
        "git", "checkout", "-b", "crew/task-75",
        "--track", "origin/crew/task-75",
    ] in calls


def test_ensure_task_branch_rejects_unexpected_names():
    import pytest

    tools_mod = require_crew("tools")
    for bad in ("main", "../escape", "crew/task-; rm -rf", "crew/other-1"):
        with pytest.raises(ValueError):
            tools_mod.ensure_task_branch(bad)


def test_ensure_task_branch_failure_raises(monkeypatch):
    import pytest

    tools_mod = require_crew("tools")
    calls: list[list[str]] = []
    _scripted_run(monkeypatch, calls, {
        ("git", "fetch", "origin", "main"): (1, "fatal: no network"),
    })
    with pytest.raises(RuntimeError):
        tools_mod.ensure_task_branch("crew/task-75")


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
