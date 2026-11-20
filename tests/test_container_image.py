"""The container image must carry a real checkout.

The builder branches, commits, pushes, and runs the suite in-container.
That needs .git/ and tests/ in the image, plus the repo root files
(ponytail.md, docs/, AGENTS.md). Regression test: the image once
shipped only crew/, so git and pytest both failed in-container and no
PR could ever open.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_dockerignore_keeps_git_and_tests() -> None:
    ignored = _read(".dockerignore").splitlines()
    assert ".git/" not in ignored
    assert "tests/" not in ignored


def test_dockerfile_copies_whole_repo() -> None:
    dockerfile = _read("Dockerfile")
    assert "COPY . ." in dockerfile
    assert "COPY crew/" not in dockerfile


def test_entrypoint_wires_git_auth() -> None:
    entrypoint = _read("entrypoint.sh")
    assert "gh auth setup-git" in entrypoint
    assert 'ENTRYPOINT ["/app/entrypoint.sh"]' in _read("Dockerfile")
