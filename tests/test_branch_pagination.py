"""Branch discovery keeps names from the end of a long listing."""

from conftest import require_crew


def test_list_branches_preserves_last_name(monkeypatch):
    tools = require_crew("tools")
    names = [f"branch-{i:04d}" for i in range(2_000)] + ["last-branch"]

    def fake_run(cmd, *, max_output):
        assert "--paginate" in cmd
        assert max_output is None
        return "\n".join(names)

    monkeypatch.setattr(tools, "_run", fake_run)
    assert tools.list_branches("warni001/agent-crew").splitlines() == names
