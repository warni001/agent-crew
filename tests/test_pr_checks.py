"""pr_checks must report CI status for a PR head via a dedicated tool.

Regression (Nov 21, 2026): the crew stalled the #48 merge task because
it could not retrieve GitHub CI results for an exact head SHA — `gh pr
checks` is blocked by run_shell's allowlist, and no CI-status tool
existed. `gh` is deliberately not in run_shell's allowlist, so the fix
is a dedicated tool, following list_open_prs.
"""

from __future__ import annotations

from unittest import mock

from conftest import require_crew

_JQ = (
    "{head: .headRefOid, checks: [(.statusCheckRollup // [])[] | "
    "{name: .name, status: .status, conclusion: .conclusion}]}"
)


def _fake_run(monkeypatch, calls, stdout):
    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        result = mock.Mock()
        result.returncode = 0
        result.communicate.return_value = (stdout, "")
        return result

    monkeypatch.setattr("subprocess.Popen", fake_run)


def test_pr_checks_calls_gh_pr_view_with_head_and_rollup(monkeypatch):
    tools_mod = require_crew("tools")
    calls: list[list[str]] = []
    _fake_run(
        monkeypatch, calls,
        '{"head": "39a2df4d080b642d1cc7fd74b6e4939c8cad74fc", '
        '"checks": [{"name": "test", "status": "COMPLETED", '
        '"conclusion": "SUCCESS"}]}',
    )
    out = tools_mod.pr_checks("warni001/agent-crew", 38)
    assert calls == [
        [
            "gh", "pr", "view", "38", "--repo", "warni001/agent-crew",
            "--json", "headRefOid,statusCheckRollup",
            "--jq", _JQ,
        ]
    ]
    assert '"head": "39a2df4d080b642d1cc7fd74b6e4939c8cad74fc"' in out
    assert "SUCCESS" in out


def test_pr_checks_is_in_builder_reviewer_and_merger_toolsets():
    """The builder, reviewer, and merger all need CI status: the
    builder verifies green CI after a branch update, the reviewer
    verifies it before signing off, and the merger verifies it
    immediately before merging."""
    tools_mod = require_crew("tools")
    for name in ("BUILD_TOOLS", "REVIEW_TOOLS", "MERGE_TOOLS", "ALL_TOOLS"):
        names = [t.name for t in getattr(tools_mod, name)]
        assert "pr_checks" in names, name
