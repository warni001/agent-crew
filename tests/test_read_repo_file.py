"""Remote reads return whole files unchanged or fail explicitly."""

from __future__ import annotations

import base64
import json
from unittest import mock

import pytest

from conftest import require_crew


@pytest.fixture
def tools_mod():
    return require_crew("tools")


def _fake_run(monkeypatch, stdout, returncode=0, stderr=""):
    runner = mock.Mock(return_value=mock.Mock(
        returncode=returncode, stdout=stdout, stderr=stderr,
    ))
    monkeypatch.setattr("subprocess.run", runner)
    return runner


def _file(contents: str) -> str:
    data = contents.encode("utf-8")
    return json.dumps({
        "type": "file", "size": len(data), "encoding": "base64",
        "content": base64.encodebytes(data).decode("ascii"),
    })


@pytest.mark.parametrize("contents", ["", "[section]\n", "  café\r\n\n", "x" * 100_000])
def test_read_repo_file_returns_exact_contents(tools_mod, monkeypatch, contents):
    _fake_run(monkeypatch, _file(contents), stderr="diagnostic\n")
    assert tools_mod.read_repo_file("warni001/agent-crew", "example") == contents


def test_read_repo_file_missing_is_file_not_found(tools_mod, monkeypatch):
    _fake_run(monkeypatch, "", returncode=1, stderr="gh: Not Found (HTTP 404)")
    with pytest.raises(FileNotFoundError):
        tools_mod.read_repo_file("warni001/agent-crew", "crew/nope.py")


def test_read_repo_file_directory_is_value_error(tools_mod, monkeypatch):
    _fake_run(monkeypatch, '[{"name": "__init__.py", "type": "file"}]')
    with pytest.raises(ValueError, match="is a directory"):
        tools_mod.read_repo_file("warni001/agent-crew", "crew")


def test_read_repo_file_rejects_oversized_content(tools_mod, monkeypatch):
    _fake_run(monkeypatch, _file("x" * 100_001))
    with pytest.raises(ValueError, match="exceeds the 100000-byte read limit"):
        tools_mod.read_repo_file("warni001/agent-crew", "example")


def test_read_repo_file_encodes_path_and_ref(tools_mod, monkeypatch):
    runner = _fake_run(monkeypatch, _file(""))
    tools_mod.read_repo_file("warni001/agent-crew", "dir/a?#.txt", "branch&ref=main")
    assert runner.call_args.args[0][-1] == (
        "repos/warni001/agent-crew/contents/dir/a%3F%23.txt?ref=branch%26ref%3Dmain"
    )
