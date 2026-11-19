# Ponytail

The coding rules for this repo. Short version: stop at the first rung
that fully works, and never build for later.

## The rungs

Work through them in order. Stop at the first rung that fully works:

1. **Does it need to exist?** If the problem goes away by deleting
   code, deleting a feature, or doing nothing — do that.
2. **Reuse the codebase.** If something in the repo already does it
   (or almost does it), use it or extend it.
3. **Stdlib / native platform.** Prefer the standard library and what
   the platform already provides over a new dependency.
4. **Installed dep.** If a dependency already in `requirements.txt`
   does it, use it. Do not add a new dependency to save a few lines.
5. **One line.** The smallest change that works is the right change.
6. **Minimum that works.** Build the smallest thing that satisfies
   the requirement — nothing more.

## Never cut

No rung justifies cutting any of these:

- Trust-boundary validation (anything crossing from untrusted input
  to trusted action gets checked)
- Data-loss handling (never silently drop, overwrite, or corrupt data)
- Security (secrets, auth, scope checks — see the scope lock)
- Accessibility (anything a human reads or clicks stays usable)
- Anything explicitly asked for

## No building for later

- No abstraction, config, or boilerplate "for later". If it is not
  needed now, it does not exist now.
- Deletion beats addition.
- Leave one small test for non-trivial logic.
- Mark shortcuts explicitly, in the code and in the PR description:

  ```
  shortcut: <limit>, <when to upgrade>
  ```

  Example: `shortcut: polls cost every 60s, not live; upgrade to the
  Cost Management API when the crew runs longer than a day.`

## Convention first

Follow established convention first; a new pattern only when
convention fails. When a pattern name exists, reuse it instead of
re-describing the behaviour. Do not invent a novel approach from
ignorance — learn the prevailing pattern first, then decide.

## End every coding reply / PR description with

What was skipped / not checked, plus the risk. Example:

> Skipped: live Foundry call (no Azure access from this machine).
> Not checked: quota behaviour above 100K TPM. Risk: first big
> prompt may throttle; the credit-check stop still applies.

The Reviewer agent checks every PR against this file. A PR that
adds abstraction for later, skips the test for non-trivial logic, or
ends without the skipped/not-checked/risk note does not get
Reviewer sign-off — and without Reviewer sign-off there is no merge.
