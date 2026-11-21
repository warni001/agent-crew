"""Auto-rollback for deploys (scripts/rollback_watch.py).

A bad crew change that passes tests ships via auto-deploy, and the loop
is the thing that broke — so it cannot repair itself. The watch step
after deploy must roll back to the last good revision when the new one
crash-loops or goes silent, and fail loudly without rolling back when
it cannot verify the signals at all.

The script is loaded from its file (scripts/ is not a package); az and
the blob read are faked through the runner, and the clock is faked so
no test ever sleeps.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rollback_watch.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("rollback_watch", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def rw():
    return _load_script()


def _ts(hour: int = 6, minute: int = 35, second: int = 0) -> datetime:
    return datetime(2026, 11, 20, hour, minute, second, tzinfo=timezone.utc)


def _entry(event: str, ts: datetime) -> dict:
    return {"ts": ts.isoformat(), "event": event}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_parse_ts_accepts_z_and_offset(rw):
    assert rw.parse_ts("2026-11-20T06:35:17+00:00") == _ts(6, 35, 17)
    assert rw.parse_ts("2026-11-20T06:35:17Z") == _ts(6, 35, 17)
    assert rw.parse_ts("garbage") is None
    assert rw.parse_ts(None) is None
    assert rw.parse_ts(123) is None


def test_has_heartbeat_counts_only_listed_events_after_since(rw):
    since = _ts(6, 34, 2)
    assert rw.has_heartbeat([_entry("task_started", _ts(6, 35, 17))], since)
    assert rw.has_heartbeat([_entry("task_finished", _ts(6, 35, 17))], since)
    assert rw.has_heartbeat([_entry("spend", _ts(6, 35, 17))], since)
    assert not rw.has_heartbeat([_entry("task_started", _ts(6, 33, 0))], since)
    # A stalled run writes task_started first, so task_stalled alone is
    # not a heartbeat signal — only the three listed events count.
    assert not rw.has_heartbeat([_entry("task_stalled", _ts(6, 35, 17))], since)
    assert not rw.has_heartbeat([{"event": "task_started"}], since)
    assert not rw.has_heartbeat([], since)


def test_replica_running_needs_a_running_replica(rw):
    assert rw.replica_running(["Running"])
    assert rw.replica_running(["Terminated", "Running"])
    assert not rw.replica_running(["Failed"])
    assert not rw.replica_running(["Terminated"])
    assert not rw.replica_running([])


@pytest.mark.parametrize(
    "running,heartbeat,expected",
    [
        (True, True, "healthy"),
        (False, True, "crash-loop"),
        (False, False, "crash-loop"),
        (True, False, "silent"),
    ],
)
def test_decide_matrix(rw, running, heartbeat, expected):
    assert rw.decide(running, heartbeat) == expected


# ---------------------------------------------------------------------------
# Fake az runner + fake clock
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: datetime):
        self.current = start

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class FakeRunner:
    """Canned az responses; records every command. The blob download
    writes the canned ledger text to the --file path, like the real az."""

    def __init__(
        self,
        replica_states: str = "Running",
        ledger_text: str = "",
        created: str = "2026-11-20T06:34:02+00:00",
        blob_url: str = "https://stexample.blob.core.windows.net/ledger/crew-ledger.jsonl",
        fail_on: tuple[str, ...] = (),
    ):
        self.replica_states = replica_states
        self.ledger_text = ledger_text
        self.created = created
        self.blob_url = blob_url
        self.fail_on = fail_on
        self.commands: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> str:
        self.commands.append(list(cmd))
        joined = " ".join(cmd)
        for needle in self.fail_on:
            if needle in joined:
                raise RuntimeError(f"fake az failure on {needle}")
        if "revision show" in joined:
            return self.created + "\n"
        if "replica list" in joined:
            return self.replica_states + "\n"
        if "containerapp show" in joined:
            return self.blob_url + "\n"
        if "blob download" in joined:
            path = cmd[cmd.index("--file") + 1]
            Path(path).write_text(self.ledger_text, encoding="utf-8")
            return ""
        if "revision activate" in joined or "revision deactivate" in joined:
            return ""
        raise AssertionError(f"unexpected command: {joined}")

    def rollback_commands(self) -> list[list[str]]:
        return [
            c
            for c in self.commands
            if "revision activate" in " ".join(c) or "revision deactivate" in " ".join(c)
        ]


def _ledger(*entries: dict) -> str:
    return "".join(json.dumps(e) + "\n" for e in entries)


def _watch(rw, runner, clock, **kwargs):
    return rw.watch(
        "rg-agent-crew",
        "ca-agent-crew",
        "ca-agent-crew--deploy-abcdef0",
        "ca-agent-crew--deploy-1234567",
        watch_seconds=60,
        poll_seconds=30,
        runner=runner,
        sleep=clock.sleep,
        now=clock.now,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Watch outcomes
# ---------------------------------------------------------------------------


def test_watch_healthy_when_running_and_heartbeat(rw):
    clock = FakeClock(_ts(6, 35, 0))
    runner = FakeRunner(ledger_text=_ledger(_entry("task_started", _ts(6, 35, 17))))
    assert _watch(rw, runner, clock) == rw.EXIT_HEALTHY
    assert runner.rollback_commands() == []


def test_watch_crash_loop_rolls_back(rw):
    clock = FakeClock(_ts(6, 35, 0))
    runner = FakeRunner(replica_states="Failed", ledger_text="")
    assert _watch(rw, runner, clock) == rw.EXIT_ROLLED_BACK
    rollback = runner.rollback_commands()
    assert [c[3] for c in rollback] == ["activate", "deactivate"]
    assert rollback[0][-1] == "ca-agent-crew--deploy-1234567"
    assert rollback[1][3] == "deactivate"
    assert rollback[1][-1] == "ca-agent-crew--deploy-abcdef0"


def test_watch_silent_rolls_back(rw):
    clock = FakeClock(_ts(6, 35, 0))
    # Running replica, but the only ledger event predates the revision.
    runner = FakeRunner(
        replica_states="Running",
        ledger_text=_ledger(_entry("task_started", _ts(6, 33, 0))),
    )
    assert _watch(rw, runner, clock) == rw.EXIT_ROLLED_BACK
    assert len(runner.rollback_commands()) == 2


def test_watch_late_heartbeat_recovers(rw):
    # Silent on the first polls, then a heartbeat appears before the
    # window ends: the deploy stands, no rollback.
    clock = FakeClock(_ts(6, 35, 0))
    runner = FakeRunner(replica_states="Running", ledger_text="")
    calls = {"n": 0}
    real_call = runner.__call__

    def flaky(cmd):
        if "blob download" in " ".join(cmd):
            calls["n"] += 1
            if calls["n"] >= 2:
                runner.ledger_text = _ledger(_entry("spend", _ts(6, 35, 45)))
        return real_call(cmd)

    assert rw.watch(
        "rg-agent-crew", "ca-agent-crew",
        "ca-agent-crew--deploy-abcdef0", "ca-agent-crew--deploy-1234567",
        watch_seconds=60, poll_seconds=30,
        runner=flaky, sleep=clock.sleep, now=clock.now,
    ) == rw.EXIT_HEALTHY
    assert runner.rollback_commands() == []


def test_watch_unreadable_ledger_fails_loud_without_rollback(rw):
    # The deploy SP has no Storage Blob Data Reader yet: the heartbeat
    # is unverifiable. Rolling back on that signal would revert healthy
    # deploys, so the watch fails loudly (exit 2) and touches nothing.
    clock = FakeClock(_ts(6, 35, 0))
    runner = FakeRunner(fail_on=("blob download",))
    assert _watch(rw, runner, clock) == rw.EXIT_CANNOT_VERIFY
    assert runner.rollback_commands() == []


def test_watch_no_previous_revision_cannot_roll_back(rw):
    clock = FakeClock(_ts(6, 35, 0))
    runner = FakeRunner(replica_states="Failed", ledger_text="")
    assert rw.watch(
        "rg-agent-crew", "ca-agent-crew",
        "ca-agent-crew--deploy-abcdef0", "",
        watch_seconds=60, poll_seconds=30,
        runner=runner, sleep=clock.sleep, now=clock.now,
    ) == rw.EXIT_CANNOT_VERIFY
    assert runner.commands == []


def test_watch_previous_equals_new_cannot_roll_back(rw):
    clock = FakeClock(_ts(6, 35, 0))
    runner = FakeRunner(replica_states="Failed", ledger_text="")
    assert rw.watch(
        "rg-agent-crew", "ca-agent-crew",
        "ca-agent-crew--deploy-abcdef0", "ca-agent-crew--deploy-abcdef0",
        watch_seconds=60, poll_seconds=30,
        runner=runner, sleep=clock.sleep, now=clock.now,
    ) == rw.EXIT_CANNOT_VERIFY
    assert runner.commands == []


def test_watch_rollback_failure_still_exits_rolled_back(rw):
    clock = FakeClock(_ts(6, 35, 0))
    runner = FakeRunner(
        replica_states="Failed", ledger_text="", fail_on=("revision activate",)
    )
    assert _watch(rw, runner, clock) == rw.EXIT_ROLLED_BACK


# ---------------------------------------------------------------------------
# Wiring: workflow + bicep
# ---------------------------------------------------------------------------


def test_deploy_workflow_records_last_good_and_watches():
    workflow = (ROOT / ".github" / "workflows" / "crew-image.yml").read_text()
    assert "Record the last good revision" in workflow
    assert "latestReadyRevisionName" in workflow
    assert "rollback_watch.py" in workflow
    assert "--previous-revision" in workflow
    assert "steps.last-good.outputs.revision" in workflow
    # The record step must run before the update mints the new revision.
    assert workflow.index("Record the last good revision") < workflow.index(
        "Update the Container App"
    )
    # The watch runs after the new revision is verified ready.
    assert workflow.index("Verify the new revision is ready") < workflow.index(
        "rollback_watch.py"
    )


def test_bicep_grants_deploy_sp_ledger_read():
    # Storage Blob Data Reader (2a2b9908-6ea1-4ae2-8e65-a410df84e7d1)
    # on the ledger storage account, conditional on the deploy SP's
    # object ID — without it the watch cannot read the heartbeat.
    bicep = (ROOT / "infra" / "main.bicep").read_text()
    assert "deployServicePrincipalId" in bicep
    assert "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1" in bicep
    assert "storageBlobDataReaderRoleId" in bicep
