"""Persist reusable scanner filter presets as a small local JSON file."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scanner.models import PATTERN_TYPES, PatternConfig

logger = logging.getLogger("fyers_avwap.presets")

FILE_VERSION = 1


class PresetError(RuntimeError):
    """A preset could not be read, validated or written."""


@dataclass(frozen=True)
class ScannerPreset:
    """A named timeframe + pattern-filter configuration."""

    name: str
    timeframe: str
    config: PatternConfig
    saved_at: str

    @classmethod
    def from_config(
        cls,
        name: str,
        timeframe: str,
        config: PatternConfig,
    ) -> ScannerPreset:
        cleaned = normalize_preset_name(name)
        if not str(timeframe).strip():
            raise PresetError("Preset timeframe cannot be empty.")
        return cls(
            name=cleaned,
            timeframe=str(timeframe),
            config=config,
            saved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def to_dict(self) -> dict[str, Any]:
        config = self.config
        atr_multiple = config.max_candle_range_atr
        return {
            "timeframe": self.timeframe,
            "min_candles": config.min_candles,
            "max_candles": config.max_candles,
            "max_close_above_pct": round(config.max_close_above_pct * 100.0, 6),
            "max_close_below_pct": round(config.max_close_below_pct * 100.0, 6),
            "max_low_below_pct": round(config.max_low_below_pct * 100.0, 6),
            "max_body_ratio": config.max_body_ratio,
            "max_window_range_pct": round(config.max_window_range_pct * 100.0, 6),
            "use_atr": atr_multiple is not None,
            "atr_multiple": atr_multiple if atr_multiple is not None else 2.0,
            "consolidation_range_pct": round(
                config.consolidation_range_pct * 100.0, 6
            ),
            "allowed_patterns": list(config.allowed_patterns),
            "require_close_above_avwap": config.require_close_above_avwap,
            "require_close_above_vwap": config.require_close_above_vwap,
            "below_reference": config.below_reference,
            "below_candle": config.below_candle,
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_dict(cls, name: str, raw: dict[str, Any]) -> ScannerPreset:
        if not isinstance(raw, dict):
            raise PresetError(f"Preset {name!r} is not a JSON object.")
        try:
            use_atr = bool(raw.get("use_atr", True))
            atr_multiple = float(raw.get("atr_multiple", 2.0))
            allowed = raw.get("allowed_patterns", list(PATTERN_TYPES))
            if not isinstance(allowed, (list, tuple)):
                raise TypeError("allowed_patterns must be a list")
            config = PatternConfig(
                max_close_above_pct=float(raw["max_close_above_pct"]) / 100.0,
                max_close_below_pct=float(raw["max_close_below_pct"]) / 100.0,
                max_low_below_pct=float(raw["max_low_below_pct"]) / 100.0,
                max_body_ratio=float(raw["max_body_ratio"]),
                min_candles=int(raw["min_candles"]),
                max_candles=int(raw["max_candles"]),
                max_window_range_pct=float(raw["max_window_range_pct"]) / 100.0,
                max_candle_range_atr=atr_multiple if use_atr else None,
                consolidation_range_pct=float(raw["consolidation_range_pct"]) / 100.0,
                allowed_patterns=tuple(str(value) for value in allowed),
                require_close_above_avwap=bool(
                    raw.get("require_close_above_avwap", False)
                ),
                require_close_above_vwap=bool(
                    raw.get("require_close_above_vwap", False)
                ),
                below_reference=str(raw.get("below_reference", "off")),
                below_candle=str(raw.get("below_candle", "previous")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PresetError(f"Preset {name!r} is invalid: {exc}") from exc
        return cls(
            name=normalize_preset_name(name),
            timeframe=str(raw.get("timeframe", "60")),
            config=config,
            saved_at=str(raw.get("saved_at", "")),
        )


def normalize_preset_name(name: str) -> str:
    cleaned = " ".join(str(name).split())
    if not cleaned:
        raise PresetError("Preset name cannot be empty.")
    if len(cleaned) > 80:
        raise PresetError("Preset name must be 80 characters or fewer.")
    return cleaned


class PresetStore:
    """Read/write presets from a single atomic JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def list(self) -> list[ScannerPreset]:
        with self._lock:
            payload = self._read()
            presets = [
                ScannerPreset.from_dict(name, raw)
                for name, raw in payload.items()
            ]
        return sorted(presets, key=lambda preset: preset.name.casefold())

    def get(self, name: str) -> ScannerPreset | None:
        cleaned = normalize_preset_name(name)
        with self._lock:
            payload = self._read()
            raw = payload.get(cleaned)
        return ScannerPreset.from_dict(cleaned, raw) if raw is not None else None

    def save(self, preset: ScannerPreset) -> None:
        with self._lock:
            payload = self._read()
            payload[preset.name] = preset.to_dict()
            self._write(payload)
        logger.info("Saved scanner preset %s", preset.name)

    def delete(self, name: str) -> bool:
        cleaned = normalize_preset_name(name)
        with self._lock:
            payload = self._read()
            if cleaned not in payload:
                return False
            del payload[cleaned]
            self._write(payload)
        logger.info("Deleted scanner preset %s", cleaned)
        return True

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise PresetError(f"Could not read presets: {exc}") from exc
        if not isinstance(raw, dict):
            raise PresetError("Preset file must contain a JSON object.")
        if raw.get("version") != FILE_VERSION:
            raise PresetError("Unsupported preset file version.")
        presets = raw.get("presets")
        if not isinstance(presets, dict):
            raise PresetError("Preset file has no valid 'presets' mapping.")
        return presets

    def _write(self, presets: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": FILE_VERSION, "presets": presets}
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
            raise PresetError(f"Could not write presets: {exc}") from exc
        finally:
            if os.path.exists(temp_name):
                os.remove(temp_name)
