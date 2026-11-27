"""read_pr must return a PR's title, description, and diff.

Regression (Nov 21, 2026): the reviewer put changes_requested on
every open PR asking for the PR description to be provided for
verification — it had no tool that returns a PR's description or
diff (read_repo_file reads repo files, pr_checks reports CI only),
so it could never verify a PR and never approve one. `gh` is not
in run_shell's allowlist, so the fix is a dedicated tool,
following pr_checks.
"""

from __future__ import annotations

from unittest import mock

from conftest import require_crew


def _fake_run(monkeypatch, calls, outputs):
    """Mock subprocess.Popen: pop one stdout per call, in order."""
    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        result = mock.Mock()
        result.returncode = 0
        result.communicate.return_value = (outputs.pop(0), "")
        return result

    monkeypatch.setattr("subprocess.Popen", fake_run)


def test_read_pr_returns_title_body_and_diff(monkeypatch):
    tools_mod = require_crew("tools")
    calls: list[list[str]] = []
    _fake_run(monkeypatch, calls, [
        "Title: Fix X\n\nThe change does Y.",
        "diff --git a/crew/x.py b/crew/x.py\n+fixed",
    ])
    out = tools_mod.read_pr("warni001/agent-crew", 35)
    assert calls == [
        [
            "gh", "pr", "view", "35", "--repo", "warni001/agent-crew",
            "--json", "title,body",
            "--jq", '"Title: " + .title + "\\n\\n" + (.body // "")',
        ],
        ["gh", "pr", "diff", "35", "--repo", "warni001/agent-crew"],
    ]
    assert "Title: Fix X" in out
    assert "The change does Y." in out
    assert "Diff:" in out
    assert "+fixed" in out


def test_read_pr_refuses_other_repos(monkeypatch):
    tools_mod = require_crew("tools")
    calls: list[list[str]] = []
    _fake_run(monkeypatch, calls, ["", ""])
    try:
        tools_mod.read_pr("someone-else/other-repo", 1)
    except ValueError as exc:
        assert "scope lock" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("read_pr did not refuse the other repo")
    assert calls == []


def test_read_pr_is_in_reviewer_toolset():
    """The reviewer is the one that needs to read a PR; the builder
    and merger already know the change or only check the sign-off."""
    tools_mod = require_crew("tools")
    names = [t.name for t in tools_mod.REVIEW_TOOLS]
    assert "read_pr" in names
    all_names = [t.name for t in tools_mod.ALL_TOOLS]
    assert "read_pr" in all_names
