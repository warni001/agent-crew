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


def test_entrypoint_passes_through_other_commands() -> None:
    # The CI smoke test runs `which gh git pytest` in the image; an
    # entrypoint that always execs the crew would feed those words to
    # the crew's arg parser instead. The git setup only runs for the
    # crew itself, and every other command is exec'd as-is.
    entrypoint = _read("entrypoint.sh")
    assert 'exec "$@"' in entrypoint
    assert '[ "$1" = "python" ]' in entrypoint


def test_ci_runs_pytest_as_a_module() -> None:
    # Bare `pytest` does not put the repo root on sys.path, so a test
    # module that imports `crew` at the top level fails collection
    # in CI while passing locally under `python -m pytest`.
    ci = _read(".github/workflows/ci.yml")
    assert "python -m pytest" in ci
    assert "run: pytest\n" not in ci


def test_entrypoint_strips_ci_extraheader() -> None:
    # The image build copies .git/ from a CI checkout, and
    # actions/checkout writes the workflow's GITHUB_TOKEN into
    # .git/config as http.extraheader. Git sends that header on every
    # request and never consults the credential helper, so the crew's
    # fine-grained PAT never gets used and every git operation fails
    # with the dead CI token. The entrypoint must strip it before the
    # crew runs, at both local and global scope, tolerating a missing
    # key (set -e would kill the entrypoint otherwise).
    entrypoint = _read("entrypoint.sh")
    assert "http.https://github.com/.extraheader" in entrypoint
    assert "--unset-all" in entrypoint
    assert "|| true" in entrypoint


def test_image_checkout_does_not_persist_credentials() -> None:
    # Same trap at the source: the default checkout persists the
    # workflow token into .git/config, and the Docker build copies
    # .git/ into the image. Every checkout in the image workflow must
    # opt out, so the image never carries a CI token.
    workflow = _read(".github/workflows/crew-image.yml")
    checkouts = workflow.count("actions/checkout@v4")
    opt_outs = sum(
        1
        for line in workflow.splitlines()
        if line.strip().startswith("persist-credentials: false")
    )
    assert checkouts >= 1
    assert opt_outs == checkouts


def test_image_workflow_never_uses_latest() -> None:
    # `:latest` is not pushed and not smoke-tested: a `:latest` pull
    # can resolve to a stale cached manifest, so the smoke test could
    # verify the wrong image. Only the sha tag is used.
    workflow = _read(".github/workflows/crew-image.yml")
    assert "ghcr.io/warni001/agent-crew:latest" not in workflow
    assert "sha-${{ github.sha }}" in workflow
