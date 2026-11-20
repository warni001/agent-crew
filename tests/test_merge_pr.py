"""merge_pr must pass an explicit merge strategy to gh.

Regression (Nov 20, 2026): the merger had an approved PR (#36) but
could not merge it — `gh pr merge` in non-interactive mode requires
an explicit --merge / --rebase / --squash flag, and merge_pr passed
none, so the command failed and the PR stayed open.
"""

from __future__ import annotations

from unittest import mock

from conftest import require_crew


def _fake_run(monkeypatch, calls):
    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        result = mock.Mock()
        result.returncode = 0
        result.stdout = "Merged pull request #36"
        result.stderr = ""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)


def test_merge_pr_passes_explicit_merge_strategy(monkeypatch):
    tools_mod = require_crew("tools")
    calls: list[list[str]] = []
    _fake_run(monkeypatch, calls)
    tools_mod.merge_pr("warni001/agent-crew", 36)
    assert calls == [
        ["gh", "pr", "merge", "36", "--merge", "--repo", "warni001/agent-crew"]
    ]
