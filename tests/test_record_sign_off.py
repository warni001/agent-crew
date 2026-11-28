"""Formal GitHub review and ledger sign-off use the same verdict."""

import os
from unittest.mock import Mock

import pytest

from crew import tools
from crew.ledger import Ledger


@pytest.fixture
def review_env(monkeypatch, tmp_path):
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("CREW_LEDGER_PATH", str(path))
    monkeypatch.setenv("REVIEWER_GITHUB_TOKEN", "reviewer-fixture")
    monkeypatch.setenv("GH_TOKEN", "crew-fixture")
    monkeypatch.setenv("GITHUB_TOKEN", "crew-fixture")
    return Ledger(path)


@pytest.mark.parametrize(("verdict", "flag"), [
    ("approved", "--approve"),
    ("changes_requested", "--request-changes"),
])
def test_review_posts_then_records_sign_off(monkeypatch, review_env, verdict, flag):
    calls = []

    def popen(cmd, *, env, **kwargs):
        calls.append((cmd, env))
        assert review_env.sign_offs(42) == []
        proc = Mock(returncode=0)
        proc.communicate.return_value = ("", "")
        return proc

    monkeypatch.setattr(tools.subprocess, "Popen", popen)
    tools.pr_checks(tools.ALLOWED_REPO, 42)
    assert "recorded" in tools.record_sign_off(42, verdict, "review notes")
    assert [entry["verdict"] for entry in review_env.sign_offs(42)] == [verdict]
    assert review_env.sign_offs(42)[0]["notes"] == "review notes"
    assert tools.check_review_sign_off(42).startswith("YES" if verdict == "approved" else "NO")
    assert calls[0][1]["GH_TOKEN"] == os.environ["GH_TOKEN"] == "crew-fixture"
    assert "REVIEWER_GITHUB_TOKEN" not in calls[0][1]
    cmd, env = calls[1]
    assert cmd == ["gh", "pr", "review", "42", "--repo", tools.ALLOWED_REPO,
                   flag, "--body=review notes"]
    assert env["GH_TOKEN"] == "reviewer-fixture"
    assert "GITHUB_TOKEN" not in env and "REVIEWER_GITHUB_TOKEN" not in env


def test_missing_review_token_never_falls_back(monkeypatch, review_env, mock_gh):
    monkeypatch.delenv("REVIEWER_GITHUB_TOKEN")
    with pytest.raises(RuntimeError, match="missing REVIEWER_GITHUB_TOKEN"):
        tools.record_sign_off(42, "approved")
    assert mock_gh == []
    assert review_env.sign_offs(42) == []


def test_failed_review_does_not_record_or_leak_output(monkeypatch, review_env, caplog):
    proc = Mock(returncode=1)
    proc.communicate.return_value = ("", "error containing reviewer-fixture")
    monkeypatch.setattr(tools.subprocess, "Popen", Mock(return_value=proc))
    with pytest.raises(RuntimeError, match="gh pr review failed") as exc:
        tools.record_sign_off(42, "approved")
    assert "reviewer-fixture" not in str(exc.value) + caplog.text
    assert review_env.sign_offs(42) == []
