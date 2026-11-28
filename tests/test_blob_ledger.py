"""Blob ledger: append-blob persistence with a local-file fallback.

Offline only — the blob client is faked; no network, no Azure login.
"""

from __future__ import annotations

import json

import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

from conftest import require_crew


class _FakeDownload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def readall(self) -> bytes:
        return self._data


class FakeAppendBlob:
    """In-memory AppendBlobClient: create/append/download, nothing else."""

    def __init__(self) -> None:
        self.data = b""
        self.created = False
        self.create_calls = 0
        self.fail_append = False
        self.fail_download = False

    def create_append_blob(self) -> None:
        self.create_calls += 1
        if self.created:
            raise ResourceExistsError("blob already exists")
        self.created = True

    def append_block(self, data: bytes) -> None:
        if self.fail_append:
            raise RuntimeError("blob write failed")
        self.data += data

    def download_blob(self) -> _FakeDownload:
        if self.fail_download:
            raise RuntimeError("blob read failed")
        if not self.created:
            raise ResourceNotFoundError("blob not found")
        return _FakeDownload(self.data)


@pytest.fixture
def blob_mod():
    return require_crew("blob_ledger")


@pytest.fixture
def ledger_mod():
    return require_crew("ledger")


def _blob(blob_mod, fake=None):
    fake = fake or FakeAppendBlob()
    return blob_mod.BlobLedger(
        "https://st-test.blob.core.windows.net/ledger/crew-ledger.jsonl",
        client=fake,
    ), fake


def test_record_appends_one_json_line_per_event(blob_mod):
    ledger, fake = _blob(blob_mod)
    entry = ledger.record("task_started", task="do the thing")
    assert entry["event"] == "task_started"
    assert entry["task"] == "do the thing"
    assert "ts" in entry
    lines = fake.data.decode("utf-8").strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "task_started"


def test_entries_round_trips_in_order(blob_mod):
    ledger, _ = _blob(blob_mod)
    ledger.record("task_started", task="a")
    ledger.record("task_finished", task="a")
    rows = ledger.entries()
    assert [r["event"] for r in rows] == ["task_started", "task_finished"]


def test_entries_empty_when_blob_missing(blob_mod):
    ledger, _ = _blob(blob_mod)
    assert ledger.entries() == []


def test_create_called_once_across_records(blob_mod):
    ledger, fake = _blob(blob_mod)
    ledger.record("task_started", task="a")
    ledger.record("task_finished", task="a")
    assert fake.create_calls == 1


def test_record_failure_propagates_for_caller_to_handle(blob_mod):
    ledger, fake = _blob(blob_mod)
    fake.fail_append = True
    with pytest.raises(RuntimeError):
        ledger.record("task_started", task="a")


def test_sign_offs_work_through_blob_entries(blob_mod):
    ledger, _ = _blob(blob_mod)
    ledger.record("review_sign_off", pr_number=3)
    ledger.record("task_started", task="a")
    assert len(ledger.sign_offs()) == 1
    assert ledger.sign_offs(pr_number=3)[0]["event"] == "review_sign_off"
    assert ledger.sign_offs(pr_number=99) == []


def test_build_ledger_no_url_uses_local_file(blob_mod, ledger_mod, tmp_path):
    cfg = _Config(blob_url="", path=str(tmp_path / "crew-ledger.jsonl"))
    built = blob_mod.build_ledger(cfg)
    assert type(built) is ledger_mod.Ledger


def test_build_ledger_with_url_uses_blob(blob_mod):
    cfg = _Config(blob_url="https://st-test.blob.core.windows.net/ledger/crew-ledger.jsonl")
    built = blob_mod.build_ledger(cfg, client=FakeAppendBlob())
    assert isinstance(built, blob_mod.BlobLedger)


def test_build_ledger_blob_failure_falls_back_to_local(
    blob_mod, ledger_mod, tmp_path, caplog
):
    fake = FakeAppendBlob()
    fake.fail_download = True
    cfg = _Config(
        blob_url="https://st-test.blob.core.windows.net/ledger/crew-ledger.jsonl",
        path=str(tmp_path / "crew-ledger.jsonl"),
    )
    with caplog.at_level("WARNING", logger="crew"):
        built = blob_mod.build_ledger(cfg, client=fake)
    assert type(built) is ledger_mod.Ledger
    assert "blob ledger unavailable" in caplog.text


class _Config:
    """Minimal stand-in for CrewConfig (only the ledger fields)."""

    def __init__(self, blob_url="", path="crew-ledger.jsonl"):
        self.ledger_blob_url = blob_url
        self.ledger_path = path


@pytest.mark.parametrize("blocked_event", ["task_started", "spend"])
def test_stuck_blob_append_does_not_block_task_deadline(
    blocked_event, blob_mod, tmp_path, monkeypatch, caplog,
):
    import asyncio
    from threading import Event
    from time import monotonic

    main = require_crew("__main__")
    spend = require_crew("spend")
    entered, release, checkpoint = Event(), Event(), Event()

    class SlowAppendBlob(FakeAppendBlob):
        def append_block(self, data):
            event = json.loads(data)["event"]
            if event == blocked_event and not entered.is_set():
                entered.set()
                if not release.wait(4):
                    raise RuntimeError("test did not release append")
            super().append_block(data)
            if event == "spend" and len(self.data.splitlines()) == 4:
                checkpoint.set()

    ledger, fake = _blob(blob_mod, SlowAppendBlob())
    config = main.CrewConfig(
        azure_openai_endpoint="https://example.openai.azure.com",
        ledger_path=str(tmp_path / "signoffs.jsonl"), task_time_limit_minutes=0.01,
    )

    async def pending(*args):
        spend.record_usage("gpt-6-2-sol", {"output_token_count": 100_000})
        # The boundary total differs from the milestone total: assert
        # the newer checkpoint cannot be overtaken by the blocked write.
        spend.record_usage("gpt-6-2-sol", {"output_token_count": 50_000})
        await asyncio.Future()

    monkeypatch.setattr(main.CrewConfig, "from_env", lambda: config)
    monkeypatch.setattr(main, "build_ledger", lambda cfg: ledger)
    monkeypatch.setattr(main, "ensure_task_branch", lambda branch, ledger_path: branch)
    monkeypatch.setattr(main, "build_roles", lambda cfg: {})
    monkeypatch.setattr(main, "run_task", pending)
    tracker = spend.SpendTracker(milestone_usd=1)
    monkeypatch.setattr(spend, "TRACKER", tracker)
    monkeypatch.setattr(main, "TRACKER", tracker)

    start = monotonic()
    try:
        assert main.main(["blocked audit write"]) == 1
        assert monotonic() - start < 2
        assert entered.is_set()
        assert not checkpoint.is_set()
        assert "blob ledger flush timed out" in caplog.text
    finally:
        release.set()
    assert checkpoint.wait(2)
    started, milestone, failed, boundary = ledger.entries()
    assert started["event"] == "task_started"
    assert milestone["event"] == boundary["event"] == "spend"
    assert failed["event"] == "task_failed"
    assert failed["reason"] == "task_time_limit"
    assert [milestone["usd"], boundary["usd"]] == [1.0, 1.5]
