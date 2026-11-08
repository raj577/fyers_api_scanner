"""Persist completed scans as a small local JSON history file."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("fyers_avwap.scan_history")

FILE_VERSION = 1
DEFAULT_MAX_ENTRIES = 200


class ScanHistoryError(RuntimeError):
    """The scan history file could not be read or written."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ScanHistoryStore:
    """Append-only scan history with atomic writes and a bounded size."""

    def __init__(self, path: Path, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.path = Path(path)
        self.max_entries = max(int(max_entries), 1)
        self._lock = threading.RLock()

    def list(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return scans newest first."""

        with self._lock:
            entries = self._read()
        entries = sorted(
            entries,
            key=lambda entry: str(entry.get("recorded_at", "")),
            reverse=True,
        )
        if limit is not None:
            entries = entries[: max(int(limit), 0)]
        return entries

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(record, dict):
            raise ScanHistoryError("Scan history record must be a JSON object.")
        entry = dict(record)
        entry.setdefault("recorded_at", _utc_now())
        entry.setdefault("id", f"{entry['recorded_at']}-{uuid.uuid4().hex[:8]}")
        with self._lock:
            entries = self._read()
            entries.append(entry)
            entries = entries[-self.max_entries :]
            self._write(entries)
        logger.info(
            "Recorded %s scan (%s matches)",
            entry.get("scanner", "unknown"),
            entry.get("matches", 0),
        )
        return entry

    def clear(self) -> None:
        with self._lock:
            self._write([])
        logger.info("Cleared scan history")

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ScanHistoryError(f"Could not read scan history: {exc}") from exc
        if not isinstance(raw, dict):
            raise ScanHistoryError("Scan history file must contain a JSON object.")
        if raw.get("version") != FILE_VERSION:
            raise ScanHistoryError("Unsupported scan history file version.")
        entries = raw.get("scans")
        if not isinstance(entries, list):
            raise ScanHistoryError("Scan history file has no valid 'scans' list.")
        return [entry for entry in entries if isinstance(entry, dict)]

    def _write(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": FILE_VERSION, "scans": entries}
        text = json.dumps(payload, indent=2, sort_keys=True)
        fd, temp_name = tempfile.mkstemp(
            dir=str(self.path.parent),
            prefix=self.path.name,
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            os.replace(temp_name, self.path)
        except OSError as exc:
            raise ScanHistoryError(f"Could not write scan history: {exc}") from exc
        finally:
            if os.path.exists(temp_name):
                os.remove(temp_name)
