"""Append-only JSON-lines ledger. The reviewer sign-off and every gate
decision land here, so a run can be audited after the fact without
re-reading chat history.

One line per event: {"ts": ..., "event": ..., ...fields}. No locking —
single-process v1.
shortcut: no file locking, single crew process only; add locking when a second writer exists.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class Ledger:
    """Append-only event log at `path` (default crew-ledger.jsonl)."""

    def __init__(self, path: str | Path = "crew-ledger.jsonl") -> None:
        self.path = Path(path)

    def record(self, event: str, **fields: object) -> dict:
        """Append one event, return the entry written."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def entries(self) -> list[dict]:
        """All entries in order; empty list when the ledger does not exist."""
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def sign_offs(self, pr_number: int | None = None) -> list[dict]:
        """Reviewer sign-off entries, optionally for one PR."""
        rows = [e for e in self.entries() if e.get("event") == "review_sign_off"]
        if pr_number is not None:
            # A stored pr_number must be an actual integer to match:
            # a JSON `true` compares equal to 1 in Python (bool is an
            # int subclass), so a malformed approval with
            # pr_number: true would otherwise count for PR #1.
            rows = [
                e for e in rows
                if type(e.get("pr_number")) is int
                and e["pr_number"] == pr_number
            ]
        return rows
