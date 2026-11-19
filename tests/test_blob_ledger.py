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
