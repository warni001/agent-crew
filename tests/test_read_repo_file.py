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


def _fake_run(monkeypatch, outputs, returncode=0, stderr=""):
    """Mock subprocess.run: one stdout per call, in order.

    `outputs` is a list of stdouts, one per gh call: the metadata
    check first, then the raw fetch. A single string is one call.
    """
    if isinstance(outputs, str):
        outputs = [outputs]
    outputs = list(outputs)
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        result = mock.Mock()
        result.returncode = returncode
        result.stdout = outputs.pop(0) if outputs else ""
        result.stderr = stderr
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    return calls


def test_read_repo_file_returns_large_file_whole(tools_mod, monkeypatch):
    big = "    indented first line\n" + "x" * 110_000 + "\n\n"
    calls = _fake_run(monkeypatch, ["file", big], stderr="gh: warning\n")
    assert tools_mod.read_repo_file("warni001/agent-crew", "crew/tools.py") == big
    assert len(calls) == 2
    assert "--jq" in calls[0]
    cmd = calls[1]
    assert any("application/vnd.github.raw" in part for part in cmd)
    assert "--jq" not in cmd and ".content" not in cmd


@pytest.mark.parametrize("stdout", ["", "  \n\n"])
def test_read_repo_file_returns_blank_files(tools_mod, monkeypatch, stdout):
    _fake_run(monkeypatch, ["file", stdout])
    assert tools_mod.read_repo_file("warni001/agent-crew", "blank.txt") == stdout


def test_read_repo_file_missing_is_file_not_found(tools_mod, monkeypatch):
    _fake_run(monkeypatch, "", returncode=1, stderr="gh: Not Found (HTTP 404)")
    with pytest.raises(FileNotFoundError):
        tools_mod.read_repo_file("warni001/agent-crew", "crew/nope.py")


def test_read_repo_file_directory_is_value_error(tools_mod, monkeypatch):
    _fake_run(monkeypatch, "dir")
    with pytest.raises(ValueError, match="is a directory"):
        tools_mod.read_repo_file("warni001/agent-crew", "crew")


def test_read_repo_file_directory_with_stderr_warning_is_value_error(
    tools_mod, monkeypatch
):
    """A successful `dir` metadata response accompanied by a stderr
    warning is still a directory: the check reads stdout alone, so
    the warning cannot corrupt the comparison."""
    calls = _fake_run(monkeypatch, ["dir"], stderr="gh: warning\n")
    with pytest.raises(ValueError, match="is a directory"):
        tools_mod.read_repo_file("warni001/agent-crew", "crew")
    assert len(calls) == 1


def test_read_repo_file_bracket_leading_file_is_not_a_directory(
    tools_mod, monkeypatch
):
    """A file whose content starts with `[` (a JSON array, a
    bracket-led line) is read as a file: the directory check comes
    from repository metadata, not from the content."""
    calls = _fake_run(monkeypatch, ["file", '[{"a": 1}]\n'])
    assert tools_mod.read_repo_file(
        "warni001/agent-crew", "data.json"
    ) == '[{"a": 1}]\n'
    assert len(calls) == 2


def test_run_raw_stdout_preserves_crlf_and_cr(tools_mod):
    """_run with raw_stdout=True preserves CRLF and lone CR: text
    mode normalizes them to LF unless the run is binary, so this
    runs a real subprocess (not a mocked stdout string) — a mock
    would never exercise the decoding. The paired default-mode call
    keeps the previous text-mode behavior: ordinary output still
    normalizes CRLF/CR to LF."""
    import sys

    cmd = [
        sys.executable, "-c",
        "import sys; sys.stdout.buffer.write(b'a\\r\\nb\\rc\\n')",
    ]
    assert tools_mod._run(cmd, raw_stdout=True) == "a\r\nb\rc\n"
    assert tools_mod._run(cmd) == "a\nb\nc"
