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
#
# The setup only runs for the crew itself (`python -m crew ...`). Any
# other command passes through untouched — the CI smoke test runs
# `which gh git pytest` in the image, and an entrypoint that always
# execs the crew would feed those words to the crew's arg parser
# instead. Never `exec python -m crew "$@"` unconditionally.
set -e

if [ "$1" = "python" ] && [ "$2" = "-m" ] && [ "$3" = "crew" ]; then
    # The image build copies .git/ from a CI checkout, and
    # actions/checkout writes the workflow's short-lived GITHUB_TOKEN
    # into .git/config as http.extraheader. Git sends that header on
    # every request and never consults the credential helper, so the
    # crew's fine-grained PAT never gets used and every git operation
    # fails with the dead CI token. Strip it so git falls through to
    # the gh helper below. `|| true`: the key may not exist.
    git -C /app config --unset-all http.https://github.com/.extraheader || true
    git config --global --unset-all http.https://github.com/.extraheader || true
    if gh auth status >/dev/null 2>&1; then
        gh auth setup-git
    fi
    git config --global user.name "John Warnicke"
    git config --global user.email "john.warnicke@gmail.com"
fi

exec "$@"
