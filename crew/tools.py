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

# Default cap for command diagnostics: ~20KB, about 5k tokens.
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


def _run(
    cmd: list[str],
    timeout: int = 120,
    max_output: int | None = _MAX_OUTPUT,
    *,
    raw_stdout: bool = False,
) -> str:
    """Run a command (no shell), raise on failure.

    Never pass secrets in `cmd` and never log the environment: `gh`
    reads GH_TOKEN from the environment itself. By default return
    stripped stdout and stderr, bounded by `max_output`. For file
    reads, `raw_stdout` returns stdout without stripping or truncating.
    """
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        message = f"command timed out after {timeout}s: {cmd[0]}"
        logging.getLogger("crew.tools").warning("command failed: %s", message)
        raise RuntimeError(message) from None
    if proc.returncode != 0:
        out = (proc.stdout + proc.stderr).strip()
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
    if raw_stdout:
        return proc.stdout
    out = (proc.stdout + proc.stderr).strip()
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
            raw_stdout=True,
        )
    except RuntimeError as exc:
        if "404" in str(exc):
            raise FileNotFoundError(f"{path!r} not found in {repo}@{ref}") from exc
        raise
    if out.lstrip().startswith("["):
        raise ValueError(f"{path!r} is a directory in {repo}@{ref}, not a file")
    return out


def list_branches(repo: str) -> str:
    """List all branch names in the repo, one per line.

    The planner and builder use this to check whether a branch already
    contains a change before creating a fresh one — a duplicate branch
    is how the Round 4 run rebuilt three changes that already existed
    on round4-merge-signoff, round4-report-opened-pr, and
    round4-readme-status.
    """
    _check_repo(repo)
    # A truncated listing can hide an existing branch and duplicate work.
    return _run(
        ["gh", "api", f"repos/{repo}/branches", "--paginate", "--jq", ".[].name"],
        max_output=None,
    )


def list_open_prs(repo: str, head: str = "") -> str:
    """List open pull requests as JSON (number, title, head branch).

    With `head`, only PRs from that branch. The builder uses this to
    check that no equivalent PR is already open before opening one
    from an existing branch. `gh` is not in run_shell's allowlist,
    so this check cannot be done with run_shell — it needs a tool.
    """
    _check_repo(repo)
    cmd = [
        "gh", "pr", "list", "--repo", repo, "--state", "open",
        "--json", "number,title,headRefName",
    ]
    if head:
        cmd += ["--head", head]
    return _run(cmd)


def pr_checks(repo: str, pr_number: int) -> str:
    """Report GitHub CI check status for a pull request, with its head SHA.

    Returns JSON with the PR head SHA and each check's name, status,
    and conclusion, so the caller can confirm CI is green on the exact
    head SHA before merging. `gh` is not in run_shell's allowlist,
    so this check cannot be done with run_shell — it needs a tool.
    """
    _check_repo(repo)
    return _run([
        "gh", "pr", "view", str(pr_number), "--repo", repo,
        "--json", "headRefOid,statusCheckRollup",
        "--jq", "{head: .headRefOid, checks: [(.statusCheckRollup // [])[] | {name: .name, status: .status, conclusion: .conclusion}]}",
    ])


def read_pr(repo: str, pr_number: int) -> str:
    """Read a pull request's title, description, and diff.

    The reviewer needs the PR description and diff to verify the
    change against the task and the Ponytail checklist, but no tool
    returned them — read_repo_file reads repo files, pr_checks
    reports CI only. Without this, the reviewer can only ask for
    the description to be provided and never approves. `gh` is not
    in run_shell's allowlist, so this check cannot be done with
    run_shell — it needs a tool.
    """
    _check_repo(repo)
    header = _run([
        "gh", "pr", "view", str(pr_number), "--repo", repo,
        "--json", "title,body",
        "--jq", '"Title: " + .title + "\\n\\n" + (.body // "")',
    ])
    diff = _run(
        ["gh", "pr", "diff", str(pr_number), "--repo", repo],
        max_output=_READ_MAX_OUTPUT,
    )
    return f"{header}\n\nDiff:\n{diff}"


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
    """Merge a pull request (gh default merge). Only after reviewer sign-off.

    gh requires an explicit strategy in non-interactive mode, so --merge
    is passed (the merge commit, gh's default); without it the command
    fails and an approved PR never merges.
    """
    _check_repo(repo)
    return _run(["gh", "pr", "merge", str(pr_number), "--merge", "--repo", repo])


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


def check_review_sign_off(pr_number: int) -> str:
    """Report whether an 'approved' reviewer sign-off is recorded for a PR."""
    from .ledger import Ledger

    sign_offs = Ledger(
        os.environ.get("CREW_LEDGER_PATH", "crew-ledger.jsonl")
    ).sign_offs(pr_number)
    approved = [s for s in sign_offs if s.get("verdict") == "approved"]
    if approved:
        return f"YES: PR #{pr_number} has an approved reviewer sign-off."
    if sign_offs:
        return f"NO: PR #{pr_number} has sign-offs but none approved: {sign_offs}"
    return f"NO: no reviewer sign-off recorded for PR #{pr_number}."


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
READ_TOOLS = [tool(f) for f in (
    read_repo_file, read_file, list_branches, list_open_prs,
)]
BUILD_TOOLS = [tool(f) for f in (
    read_repo_file, read_file, write_file, run_pytest, run_shell,
    open_pr, list_branches, list_open_prs, pr_checks,
)]
REVIEW_TOOLS = [tool(f) for f in (
    read_repo_file, read_file, comment_on_pr, record_sign_off, pr_checks,
    read_pr,
)]
MERGE_TOOLS = [tool(f) for f in (
    merge_pr, comment_on_pr, check_review_sign_off, pr_checks,
)]
ALL_TOOLS = [tool(f) for f in (
    read_repo_file, open_pr, comment_on_pr, merge_pr,
    read_file, write_file, run_pytest, run_shell,
    record_sign_off, check_review_sign_off,
    list_branches, list_open_prs, pr_checks, read_pr,
)]

__all__ = [
    "read_repo_file", "open_pr", "comment_on_pr", "merge_pr",
    "read_file", "write_file", "run_pytest", "run_shell",
    "record_sign_off", "check_review_sign_off",
    "list_branches", "list_open_prs", "pr_checks", "read_pr",
    "ALLOWED_REPOS", "GHCR_IMAGE", "image_for_commit", "assert_repo_allowed",
    "READ_TOOLS", "BUILD_TOOLS", "REVIEW_TOOLS", "MERGE_TOOLS", "ALL_TOOLS",
]
