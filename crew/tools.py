"""Crew tools as plain Python functions, exposed to the agents via
agent-framework's ``tool()`` wrapper (see the ``*_TOOLS`` lists below).

GHCR RULE (repo owner, explicit): crew code must NOT attempt a direct
GHCR push with its fine-grained PAT — the PAT has no Packages permission
and a direct push would fail. GHCR push happens only via the GitHub
Actions workflow (GITHUB_TOKEN, packages: write). There is deliberately
no docker-push tool or function in this module; do not add one.

Scope lock: every GitHub function refuses any repo other than
warni001/agent-crew. The shell tool only runs an allowlisted prefix set
and rejects shell metacharacters, so a prompt-injected command cannot
escape it. No tool ever prints or returns a token.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path

from agent_framework import tool

from .config import ALLOWED_REPO

# Default cap for a command's combined output: ~20KB, about 5k tokens.
# 4000 was far too small — a failing pytest run or a long listing
# was cut before the useful part, and file reads broke entirely.
# Commands whose full output is the point (file reads) pass a
# larger cap explicitly; this default only bounds the rest.
_MAX_OUTPUT = 20_000
_SHELL_FORBIDDEN = (";", "&&", "||", "|", ">", "<", "`", "$(")
# Builder needs git, tests, and read-only inspection — nothing else.
_SHELL_ALLOWED_PREFIXES = (
    "git ",
    "pytest",
    "python -m pytest",
    "python -m compileall",
    "python -m pip list",
    "ls ",
    "cat ",
    "pwd",
)


def _run(cmd: list[str], timeout: int = 120, max_output: int | None = _MAX_OUTPUT) -> str:
    """Run a command (no shell), return stdout, raise on failure.

    Never pass secrets in `cmd` and never log the environment: `gh`
    reads GH_TOKEN from the environment itself. Output is truncated
    to `max_output` chars (default 4000) — pass a larger cap, or
    None for no cap, when the full output is the point (file reads).
    """
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        message = f"command timed out after {timeout}s: {cmd[0]}"
        logging.getLogger("crew.tools").warning("command failed: %s", message)
        raise RuntimeError(message) from None
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        # Log the failure here, with the command and its output: the
        # framework catches this exception and hands the model a
        # generic "Error: Function failed." (unless detailed errors
        # are on), so without this log the container shows only an
        # opaque RuntimeError — the Nov 20 #9 run stalled on two of
        # those and nobody could see what the commands even were.
        message = (
            f"{' '.join(cmd[:3])} failed (exit {proc.returncode}): "
            f"{out[:_MAX_OUTPUT]}"
        )
        logging.getLogger("crew.tools").warning("command failed: %s", message)
        raise RuntimeError(message)
    return out if max_output is None else out[:max_output]


ALLOWED_REPOS = frozenset({ALLOWED_REPO})
# The image repository, with no tag: tags are always built per commit
# by image_for_commit below. `:latest` is never used — not for deploys
# and not for verification (a `:latest` pull can resolve stale).
GHCR_IMAGE = "ghcr.io/warni001/agent-crew"


def image_for_commit(sha: str) -> str:
    """The image tag to deploy for a commit: an immutable sha tag.

    Never deploy `:latest`: updating with an unchanged `:latest` string
    mints no new Container Apps revision, and a `:latest` pull can
    resolve to a stale cached manifest. A sha tag can only ever point
    at one digest, so both problems go away. Pair it with a unique
    `--revision-suffix` on the update.
    """
    sha = sha.strip()
    if not sha:
        raise ValueError("commit sha must not be empty")
    return f"{GHCR_IMAGE}:sha-{sha}"


def _check_repo(repo: str) -> None:
    if repo != ALLOWED_REPO:
        raise ValueError(
            f"scope lock: crew works only in {ALLOWED_REPO}, not {repo!r}"
        )


def assert_repo_allowed(repo: str) -> None:
    """Public scope-lock check: raise ValueError for any other repo."""
    _check_repo(repo)


def _repo_root() -> Path:
    return Path.cwd().resolve()


def _inside_repo(path: str) -> Path:
    target = (_repo_root() / path).resolve()
    if not target.is_relative_to(_repo_root()):
        raise ValueError(f"refusing path outside the repo checkout: {path!r}")
    return target


# Cap for a single file read: ~100KB of source. Bounds a pathological
# read while covering every file in this repo with room to spare.
_READ_MAX_OUTPUT = 100_000


def read_repo_file(repo: str, path: str, ref: str = "main") -> str:
    """Read a file from the repo at a ref, via the gh CLI (raw content).

    Raw, not base64: the old path decoded `.content`, and `_run`'s
    4000-char cap cut any file over ~3KB mid-string, so b64decode
    failed with 'Incorrect padding' and the crew could not read its
    own larger files (tools.py, roles.py, config.py). A 404 is a
    FileNotFoundError; a directory path is a ValueError.
    """
    _check_repo(repo)
    try:
        out = _run(
            ["gh", "api", "-H", "Accept: application/vnd.github.raw",
             f"repos/{repo}/contents/{path}?ref={ref}"],
            max_output=_READ_MAX_OUTPUT,
        )
    except RuntimeError as exc:
        if "404" in str(exc):
            raise FileNotFoundError(f"{path!r} not found in {repo}@{ref}") from exc
        raise
    if not out.strip():
        raise FileNotFoundError(f"{path!r} not found in {repo}@{ref}")
    if out.lstrip().startswith("["):
        raise ValueError(f"{path!r} is a directory in {repo}@{ref}, not a file")
    return out


def open_pr(repo: str, title: str, body: str, head: str, base: str = "main") -> str:
    """Open a pull request. Returns the PR URL. Call only after tests pass."""
    _check_repo(repo)
    return _run(
        ["gh", "pr", "create", "--repo", repo, "--head", head,
         "--base", base, "--title", title, "--body", body]
    )


def comment_on_pr(repo: str, pr_number: int, body: str) -> str:
    """Comment on a pull request."""
    _check_repo(repo)
    return _run(
        ["gh", "pr", "comment", str(pr_number), "--repo", repo, "--body", body]
    )


def merge_pr(repo: str, pr_number: int) -> str:
    """Merge a pull request (gh default merge). Only after reviewer sign-off."""
    _check_repo(repo)
    if (reason := _review_blocker(pr_number)) is not None:
        raise RuntimeError(f"refusing to merge PR #{pr_number}: {reason}")
    return _run(["gh", "pr", "merge", str(pr_number), "--repo", repo])


def record_sign_off(pr_number: int, verdict: str, notes: str = "") -> str:
    """Record a reviewer sign-off in the ledger.

    verdict is 'approved' or 'changes_requested'. Only 'approved' lets
    the merger proceed. The ledger path comes from CREW_LEDGER_PATH.
    """
    if verdict not in ("approved", "changes_requested"):
        raise ValueError(f"verdict must be 'approved' or 'changes_requested', got {verdict!r}")
    from .ledger import Ledger

    entry = Ledger(os.environ.get("CREW_LEDGER_PATH", "crew-ledger.jsonl")).record(
        "review_sign_off", pr_number=pr_number, verdict=verdict, notes=notes
    )
    return f"recorded {verdict} for PR #{pr_number} at {entry['ts']}"


def _review_blocker(pr_number: int) -> str | None:
    """Return why the latest sign-off does not permit a merge, if blocked."""
    from .ledger import Ledger

    try:
        sign_offs = Ledger(
            os.environ.get("CREW_LEDGER_PATH", "crew-ledger.jsonl")
        ).sign_offs(pr_number)
    except (OSError, ValueError, AttributeError):
        return "reviewer sign-off ledger is unreadable"
    if not sign_offs:
        return "no reviewer sign-off recorded"
    if sign_offs[-1].get("verdict") != "approved":
        return "latest reviewer sign-off is not approved"
    return None


def check_review_sign_off(pr_number: int) -> str:
    """Report whether the latest reviewer sign-off permits a merge."""
    if (reason := _review_blocker(pr_number)) is not None:
        return f"NO: PR #{pr_number}: {reason}."
    return f"YES: PR #{pr_number} has an approved latest reviewer sign-off."


def read_file(path: str) -> str:
    """Read a file from the local checkout (must stay inside the repo)."""
    return _inside_repo(path).read_text(encoding="utf-8")


def write_file(path: str, content: str) -> str:
    """Write a file in the local checkout (must stay inside the repo)."""
    target = _inside_repo(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {path} ({len(content)} bytes)"


def run_pytest(extra_args: str = "") -> str:
    """Run the repo test suite. Returns pytest output; raises on failure."""
    cmd = [sys.executable, "-m", "pytest", "-q", *shlex.split(extra_args)]
    return _run(cmd, timeout=300)


def run_shell(command: str) -> str:
    """Run one allowlisted shell command (git, pytest, compileall, ls, cat).

    Rejects shell metacharacters, so chained commands cannot smuggle
    anything past the allowlist. Refusals are logged, not just raised:
    a refusal is a failed tool call too, and the framework's log line
    for it carries no detail.
    """
    if any(tok in command for tok in _SHELL_FORBIDDEN):
        logging.getLogger("crew.tools").warning(
            "run_shell refused command with shell metacharacters: %r", command
        )
        raise ValueError(f"refusing command with shell metacharacters: {command!r}")
    if not command.startswith(_SHELL_ALLOWED_PREFIXES):
        logging.getLogger("crew.tools").warning(
            "run_shell refused command outside the allowlist: %r", command
        )
        raise ValueError(
            "refusing command outside the allowlist "
            f"(git/pytest/compileall/ls/cat/pwd): {command!r}"
        )
    return _run(shlex.split(command), timeout=300)


# Framework wrappers. Plain functions above stay directly callable (and
# testable without the framework); these are what the agents receive.
# NOTE: there is deliberately no check_credit tool. Credit is enforced
# in code by the loop (orchestration.run_task refuses to start a task
# past the budget/deadline) — the agents must never check it themselves:
# the instruction to check before acting looped forever, each check
# re-sending full history as prompt tokens, and repeated checks
# preceded a 400 about a missing tool call.
READ_TOOLS = [tool(f) for f in (read_repo_file, read_file)]
BUILD_TOOLS = [tool(f) for f in (
    read_repo_file, read_file, write_file, run_pytest, run_shell,
)]
REVIEW_TOOLS = [tool(f) for f in (
    read_repo_file, read_file, comment_on_pr, record_sign_off,
)]
MERGE_TOOLS = [tool(f) for f in (
    merge_pr, comment_on_pr, check_review_sign_off,
)]
ALL_TOOLS = [tool(f) for f in (
    read_repo_file, open_pr, comment_on_pr, merge_pr,
    read_file, write_file, run_pytest, run_shell,
    record_sign_off, check_review_sign_off,
)]

__all__ = [
    "read_repo_file", "open_pr", "comment_on_pr", "merge_pr",
    "read_file", "write_file", "run_pytest", "run_shell",
    "record_sign_off", "check_review_sign_off",
    "ALLOWED_REPOS", "GHCR_IMAGE", "image_for_commit", "assert_repo_allowed",
    "READ_TOOLS", "BUILD_TOOLS", "REVIEW_TOOLS", "MERGE_TOOLS", "ALL_TOOLS",
]
