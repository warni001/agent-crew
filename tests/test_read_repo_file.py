"""read_repo_file must return whole files, and fail cleanly otherwise.

Regression (Nov 19, 2026): `_run` capped all output at 4000 chars,
and the old base64 path decoded `.content` — so any file over ~3KB
came back cut mid-string and b64decode failed with 'Incorrect
padding'. The crew could read small files (credit.py) but not its
own larger ones (tools.py, roles.py, config.py), and every one of
those reads surfaced as a bare RuntimeError.
"""

from __future__ import annotations

from unittest import mock

import pytest

from conftest import require_crew


@pytest.fixture
def tools_mod():
    return require_crew("tools")


def _fake_run(monkeypatch, stdout, returncode=0, stderr=""):
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        result = mock.Mock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = stderr
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    return calls


def test_read_repo_file_returns_large_file_whole(tools_mod, monkeypatch):
    big = "    indented first line\n" + "x" * 110_000 + "\n\n"
    calls = _fake_run(monkeypatch, big, stderr="gh: warning\n")
    assert tools_mod.read_repo_file("warni001/agent-crew", "crew/tools.py") == big
    cmd = calls[0]
    assert any("application/vnd.github.raw" in part for part in cmd)
    assert "--jq" not in cmd and ".content" not in cmd


@pytest.mark.parametrize("stdout", ["", "  \n\n"])
def test_read_repo_file_returns_blank_files(tools_mod, monkeypatch, stdout):
    _fake_run(monkeypatch, stdout)
    assert tools_mod.read_repo_file("warni001/agent-crew", "blank.txt") == stdout


def test_read_repo_file_missing_is_file_not_found(tools_mod, monkeypatch):
    _fake_run(monkeypatch, "", returncode=1, stderr="gh: Not Found (HTTP 404)")
    with pytest.raises(FileNotFoundError):
        tools_mod.read_repo_file("warni001/agent-crew", "crew/nope.py")


def test_read_repo_file_directory_is_value_error(tools_mod, monkeypatch):
    _fake_run(monkeypatch, '[{"name": "__init__.py"}]')
    with pytest.raises(ValueError, match="is a directory"):
        tools_mod.read_repo_file("warni001/agent-crew", "crew")
