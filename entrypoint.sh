#!/bin/sh
# Container entrypoint: wire git auth, then run the crew.
#
# `gh auth setup-git` makes git use gh as its credential helper, so the
# builder's `git push` authenticates with the runtime GH_TOKEN (Key
# Vault secret, never baked into the image). Without it the builder can
# commit but never push, so no PR can ever open. Only configured when
# gh is actually authed; otherwise the crew still runs and a push fails
# with a clear error. Git identity is the repo rule: commits in
# agent-crew go as warni001.
set -e

if gh auth status >/dev/null 2>&1; then
    gh auth setup-git
fi
git config --global user.name "John Warnicke"
git config --global user.email "john.warnicke@gmail.com"

exec python -m crew "$@"
