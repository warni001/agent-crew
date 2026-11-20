# syntax=docker/dockerfile:1
FROM python:3.14-slim

# No secrets in this image: credentials reach the container at runtime
# via the Container App's managed identity and Key Vault, never baked in.

WORKDIR /app

# Tools the crew calls and the slim base image does not ship: git (the
# builder's shell tool), the GitHub CLI (every gh-based tool), and pytest
# (the builder runs the suite in-container). The gh CLI comes from its
# official apt repository; pytest is pinned to the same version CI uses.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir pytest==9.2.0

# The whole repo context, not just crew/: the builder works in a real
# checkout — it branches, commits, and pushes (needs .git/), runs the
# suite (needs tests/), and reads ponytail.md, docs/, and AGENTS.md.
# Exclusions (.env, .venv, .github, infra) live in .dockerignore.
# .git/ is ~1.6M; the Actions checkout is shallow, which is fine for
# branching and pushing.
COPY . .

# /app was built as root; the crew runs as appuser and must be able to
# write here (the ledger, and any file the builder writes). Without
# this, the first ledger write dies with PermissionError and the
# container restarts in a loop.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app \
    && chmod +x /app/entrypoint.sh
USER appuser

# The entrypoint wires git auth (gh as credential helper) and git
# identity, then runs the crew. No task argument: the crew runs its
# default self-build task first, then polls GitHub issues labelled
# `crew-task` every 60 seconds.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "crew"]
