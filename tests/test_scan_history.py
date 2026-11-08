"""Unit tests for the local scan history store."""

from __future__ import annotations

from pathlib import Path

import pytest

from data.scan_history import ScanHistoryError, ScanHistoryStore


def _record(index: int) -> dict[str, object]:
    return {
        "recorded_at": f"2026-01-05T09:0{index}:00+00:00",
        "scanner": "vwap_reclaim",
        "timeframe": "15m",
        "scanned": 10,
        "matches": index,
        "signals": [],
    }


def test_append_and_list_newest_first(tmp_path: Path) -> None:
    store = ScanHistoryStore(tmp_path / "scan_history.json")
    store.append(_record(1))
    store.append(_record(2))

    entries = store.list()

    assert [entry["matches"] for entry in entries] == [2, 1]
    assert entries[0]["id"]


def test_history_is_capped(tmp_path: Path) -> None:
    store = ScanHistoryStore(tmp_path / "scan_history.json", max_entries=2)
    for index in range(1, 5):
        store.append(_record(index))

    entries = store.list()

    assert len(entries) == 2
    assert [entry["matches"] for entry in entries] == [4, 3]


def test_clear_removes_entries(tmp_path: Path) -> None:
    store = ScanHistoryStore(tmp_path / "scan_history.json")
    store.append(_record(1))
    store.clear()

    assert store.list() == []


def test_corrupt_history_raises(tmp_path: Path) -> None:
    path = tmp_path / "scan_history.json"
    path.write_text("{not json", encoding="utf-8")
    store = ScanHistoryStore(path)

    with pytest.raises(ScanHistoryError):
        store.list()
