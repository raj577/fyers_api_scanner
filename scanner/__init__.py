"""AVWAP pattern detection and scan orchestration.

Exports are loaded lazily so data-layer modules can import ``scanner.models``
without creating a package import cycle.
"""

from __future__ import annotations

from typing import Any

_MODEL_EXPORTS = {
    "PATTERN_CONSOLIDATION",
    "PATTERN_HUGGING",
    "PATTERN_RECLAIM",
    "PATTERN_TOUCH",
    "PATTERN_TYPES",
    "DetectedPattern",
    "Instrument",
    "PatternConfig",
    "ScanError",
    "ScanMatch",
    "ScanReport",
}

_LAZY_EXPORTS = {
    "PatternDetector": ("scanner.pattern_detector", "PatternDetector"),
    "PresetError": ("scanner.presets", "PresetError"),
    "PresetStore": ("scanner.presets", "PresetStore"),
    "ScannerPreset": ("scanner.presets", "ScannerPreset"),
    "compute_candle_metrics": ("scanner.pattern_detector", "compute_candle_metrics"),
    "small_candle_mask": ("scanner.pattern_detector", "small_candle_mask"),
    "ScanEngine": ("scanner.scan_engine", "ScanEngine"),
}

_ALL_EXPORTS = sorted(_MODEL_EXPORTS | set(_LAZY_EXPORTS))
__all__ = list(_ALL_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _MODEL_EXPORTS:
        from scanner import models

        return getattr(models, name)
    if name in _LAZY_EXPORTS:
        module_name, attribute = _LAZY_EXPORTS[name]
        module = __import__(module_name, fromlist=[attribute])
        return getattr(module, attribute)
    raise AttributeError(f"module 'scanner' has no attribute {name!r}")
