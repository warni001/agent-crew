"""Repo hygiene: no secrets in files, .env never committed.

Runs standalone. Scans tracked files (plus the new tests/docs) for
literal secret patterns and confirms the gitignore covers ``.env``.
This is the tripwire for the "no secrets in code" rule — the config
tests cover the runtime side of the same rule.
"""

from __future__ import annotations

import re
import subprocess

SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    # Literal quoted assignment only: the value's first char must not be
    # "$" (command substitution / variable, e.g. PASSWORD="$(az ...)"
    # in infra/deploy.sh, which holds a freshly reset credential at
    # runtime and unsets it — not a hardcoded secret) or "<" (a
    # "<redacted>" placeholder). A real hardcoded secret is a bare
    # literal of 8+ characters.
    re.compile(r"(?i)(api[_-]?key|password|client[_-]?secret)\s*=\s*['\"][^'\"$<][^'\"]{7,}['\"]"),
]

# Files allowed to mention a token *pattern* in prose (docs/tests that
# name the shape, never a value). Checked by pattern, not by filename
# exemption for real values: the regexes above only match real values.
SKIP_DIRS = {".git", ".venv", "__pycache__"}


def _tracked_files(repo_root):
    out = subprocess.run(
        ["git", "ls-files"], cwd=repo_root, capture_output=True, text=True, check=True
    )
    return [repo_root / line for line in out.stdout.splitlines() if line.strip()]


def test_no_secret_values_in_tracked_files(repo_root):
    offenders = []
    for path in _tracked_files(repo_root):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(repo_root)}: {pattern.pattern}")
    assert not offenders, "secret-like values found:\n" + "\n".join(offenders)


def test_no_secret_values_in_new_unstaged_files(repo_root):
    """Also scan untracked-but-present worktree files (tests/docs)."""
    offenders = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in {".pyc"}:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(repo_root)}: {pattern.pattern}")
    assert not offenders, "secret-like values found:\n" + "\n".join(offenders)


def test_env_files_gitignored(repo_root):
    gitignore = (repo_root / ".gitignore").read_text()
    assert ".env" in gitignore


def test_no_dotenv_file_in_repo(repo_root):
    assert not (repo_root / ".env").exists()
    assert not list(repo_root.glob(".env.*")) or all(
        p.name == ".env.example" for p in repo_root.glob(".env.*")
    )
