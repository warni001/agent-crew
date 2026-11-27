"""Ledger backed by an Azure append blob, with a local-file fallback.

Same interface as crew.ledger.Ledger (record/entries/sign_offs) so the
run loop uses either one unchanged. One JSON line per event, appended
with append_block — the append-blob shape fits the JSONL ledger
exactly, and there is a single writer, so no concurrency handling.

Auth is the container app's SystemAssigned identity
(DefaultAzureCredential); the bicep grants it Storage Blob Data
Contributor on the storage account. Cost is negligible at this scale
(Hot tier: ~$0.0184/GB/month, $0.05/10k writes): the ledger is
kilobytes and the crew writes a handful of events per task.

A blob failure never kills a run: build_ledger() probes the blob at
startup and falls back to the local file, and record failures are
already best-effort in the run loop (a warning, not a crash).

shortcut: no rotation — an append blob caps at 50,000 appends. At the
crew's write rate that is years away; rotate (new blob, carry the
done-task set over) when entries approach 45,000.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from queue import Full, Queue
from threading import Event, Thread

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobClient

from .ledger import Ledger

log = logging.getLogger("crew")


class BlobLedger(Ledger):
    """Append-only event log in an append blob.

    `fallback_path` is only the local file the ledger falls back to
    when the blob is unreachable — BlobLedger itself never writes it.
    Pass `client` (a fake in tests) to skip building a real client.
    (azure-storage-blob 12.31 dropped the separate AppendBlobClient:
    BlobClient carries create_append_blob/append_block itself.)
    """

    def __init__(
        self,
        blob_url: str,
        fallback_path: str = "crew-ledger.jsonl",
        client: BlobClient | None = None,
    ) -> None:
        super().__init__(fallback_path)
        self.blob_url = blob_url
        self._client = client or BlobClient.from_blob_url(
            blob_url, credential=DefaultAzureCredential()
        )
        self._blob_ready = False
        self._writes = Queue(maxsize=64)
        self._writer: Thread | None = None

    def enqueue(self, event: str, fields: dict, *, wait: float = 0) -> None:
        """Queue a run-loop event; only boundary checkpoints wait briefly.

        The run loop is the sole producer and uses this for all writes.
        shortcut: a prolonged outage or process exit can lose pending events;
        add a durable spool if replay across restarts is required.
        """
        if self._writer is None:
            writer = Thread(target=self._write_pending, name="blob-ledger", daemon=True)
            writer.start()
            self._writer = writer
        done = Event() if wait else None
        # Capture the time of the event, not when the worker reaches Azure.
        row = {"ts": datetime.now(timezone.utc).isoformat(), **fields}
        try:
            self._writes.put_nowait((event, row, done))
        except Full:
            log.warning("blob ledger queue full; dropped %s", event)
            return
        if done is not None and not done.wait(wait):
            log.warning("blob ledger flush timed out at %s", event)

    def _write_pending(self) -> None:
        while True:
            event, fields, done = self._writes.get()
            try:
                self.record(event, **fields)
            except Exception as exc:  # noqa: BLE001 - audit failure is best effort
                log.warning("ledger %s failed: %s", event, exc)
            finally:
                if done is not None:
                    done.set()

    def record(self, event: str, **fields: object) -> dict:
        """Append one event to the blob, return the entry written."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        if not self._blob_ready:
            try:
                self._client.create_append_blob()
            except ResourceExistsError:
                pass
            self._blob_ready = True
        self._client.append_block(json.dumps(entry).encode("utf-8") + b"\n")
        return entry

    def entries(self) -> list[dict]:
        """All entries in order; empty list when the blob does not exist."""
        try:
            raw = self._client.download_blob().readall()
        except ResourceNotFoundError:
            return []
        out = []
        for line in raw.decode("utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out


def build_ledger(config, client: BlobClient | None = None) -> Ledger:
    """Blob ledger when CREW_LEDGER_BLOB_URL is set, else the local file.

    A blob that cannot be reached (auth, network, missing account)
    warns once and falls back to the local file — the ledger is an
    audit trail, not the work itself.
    """
    if not config.ledger_blob_url:
        return Ledger(config.ledger_path)
    try:
        ledger = BlobLedger(
            config.ledger_blob_url,
            fallback_path=config.ledger_path,
            client=client,
        )
        ledger.entries()  # Probe: fail fast here, not mid-task.
        return ledger
    except Exception as exc:  # noqa: BLE001 - any blob failure falls back
        log.warning("blob ledger unavailable (%s); using local file", exc)
        return Ledger(config.ledger_path)
