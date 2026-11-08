"""Contract-start AVWAP touch / hugging / consolidation detector.

The detector evaluates the right edge of a completed-candle series. It looks
backwards over at most ``max_candles`` small candles and returns the longest
qualifying window (shortening from the oldest side if the whole run is too
wide). A single qualifying candle is enough when ``min_candles == 1``.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from scanner.models import (
    PATTERN_CONSOLIDATION,
    PATTERN_HUGGING,
    PATTERN_RECLAIM,
    PATTERN_TOUCH,
    DetectedPattern,
    PatternConfig,
)

logger = logging.getLogger("fyers_avwap.pattern")


def compute_candle_metrics(candles: pd.DataFrame) -> pd.DataFrame:
    """Return body size, range and body-ratio columns for each candle."""
    required = {"open", "high", "low", "close"}
    missing = required - set(candles.columns)
    if missing:
        raise ValueError(f"Candle data is missing columns: {sorted(missing)}")

    body = (candles["close"] - candles["open"]).abs()
    candle_range = (candles["high"] - candles["low"]).clip(lower=0.0)
    ratio = np.where(candle_range > 0, body / candle_range, 0.0)
    return pd.DataFrame(
        {
            "body_size": body,
            "candle_range": candle_range,
            "body_ratio": np.clip(ratio, 0.0, 1.0),
        },
        index=candles.index,
    )


def small_candle_mask(
    candles: pd.DataFrame,
    max_body_ratio: float = 0.35,
    max_range_atr: float | None = 2.0,
) -> pd.Series:
    """Boolean mask of candles with small bodies (and optionally small range vs ATR)."""
    metrics = compute_candle_metrics(candles)
    mask = metrics["body_ratio"] <= float(max_body_ratio)
    if max_range_atr is not None and "atr" in candles.columns:
        atr = pd.to_numeric(candles["atr"], errors="coerce")
        valid_atr = atr.notna() & (atr > 0)
        range_ok = ~valid_atr | (metrics["candle_range"] <= atr * float(max_range_atr))
        mask = mask & range_ok
    return mask.fillna(False)


class PatternDetector:
    def __init__(self, config: PatternConfig | None = None) -> None:
        self.config = config or PatternConfig()

    def detect(self, candles: pd.DataFrame) -> DetectedPattern | None:
        """Detect a pattern ending at the final candle, or return ``None``."""

        config = self.config
        if candles is None or candles.empty or len(candles) < config.min_candles:
            return None
        missing = {"open", "high", "low", "close", "avwap"} - set(candles.columns)
        if missing:
            raise ValueError(f"Pattern detection needs columns: {sorted(missing)}")

        frame = candles.reset_index(drop=True)
        avwap = pd.to_numeric(frame["avwap"], errors="coerce")
        if not np.isfinite(avwap.iloc[-1]) or float(avwap.iloc[-1]) <= 0:
            return None

        if not self._passes_close_filters(frame, float(avwap.iloc[-1])):
            return None

        if not self._passes_below_filters(frame):
            return None

        metrics = compute_candle_metrics(frame)
        close = frame["close"].astype("float64")
        low = frame["low"].astype("float64")

        eligible = (
            avwap.notna()
            & (avwap > 0)
            & (close >= avwap * (1.0 - config.max_close_below_pct))
            & (close <= avwap * (1.0 + config.max_close_above_pct))
            & (low >= avwap * (1.0 - config.max_low_below_pct))
            & (metrics["body_ratio"] <= config.max_body_ratio)
        )
        if config.max_candle_range_atr is not None and "atr" in frame.columns:
            atr = pd.to_numeric(frame["atr"], errors="coerce")
            valid_atr = atr.notna() & (atr > 0)
            range_ok = ~valid_atr | (
                metrics["candle_range"] <= atr * float(config.max_candle_range_atr)
            )
            eligible = eligible & range_ok
        eligible = eligible.fillna(False).to_numpy(dtype=bool)

        if not eligible[-1]:
            return None

        # Longest contiguous run of eligible candles ending at the last candle.
        first_allowed = max(0, len(frame) - config.max_candles)
        run_start = len(frame) - 1
        while run_start - 1 >= first_allowed and eligible[run_start - 1]:
            run_start -= 1
        run_count = len(frame) - run_start
        if run_count < config.min_candles:
            return None

        end_idx = len(frame) - 1
        avwap_end = float(avwap.iloc[end_idx])

        # Shorten from the oldest side until the whole pattern is narrow enough.
        chosen_start = None
        for count in range(run_count, config.min_candles - 1, -1):
            start = end_idx - count + 1
            window = frame.iloc[start : end_idx + 1]
            window_range = (window["high"].max() - window["low"].min()) / avwap_end
            if window_range <= config.max_window_range_pct:
                chosen_start = start
                break
        if chosen_start is None:
            return None

        window = frame.iloc[chosen_start : end_idx + 1]
        window_avwap = avwap.iloc[chosen_start : end_idx + 1]
        window_metrics = metrics.iloc[chosen_start : end_idx + 1]
        candle_count = len(window)
        last_close = float(window["close"].iloc[-1])
        distance_pct = (last_close / avwap_end - 1.0) * 100.0
        window_range_pct = float(
            (window["high"].max() - window["low"].min()) / avwap_end
        )
        avg_body_ratio = float(window_metrics["body_ratio"].mean())
        max_body_ratio = float(window_metrics["body_ratio"].max())

        touched = bool(
            (
                (window["low"] <= window_avwap * (1.0 + config.touch_tolerance_pct))
                & (window["high"] >= window_avwap * (1.0 - config.touch_tolerance_pct))
            ).any()
        )

        pattern_type = self._classify(
            candle_count=candle_count,
            window=window,
            window_avwap=window_avwap,
            last_close=last_close,
            avwap_end=avwap_end,
            window_range_pct=window_range_pct,
            touched=touched,
        )
        if pattern_type is None or pattern_type not in config.allowed_patterns:
            return None

        first_avwap_idx = int(np.flatnonzero(avwap.notna().to_numpy())[0]) if avwap.notna().any() else 0
        anchor_timestamp = (
            pd.Timestamp(frame["timestamp"].iloc[first_avwap_idx])
            if "timestamp" in frame.columns
            else None
        )
        pattern_start = (
            pd.Timestamp(window["timestamp"].iloc[0]) if "timestamp" in window.columns else None
        )
        pattern_end = (
            pd.Timestamp(window["timestamp"].iloc[-1]) if "timestamp" in window.columns else None
        )
        latest_atr = None
        if "atr" in frame.columns and pd.notna(frame["atr"].iloc[-1]):
            latest_atr = float(frame["atr"].iloc[-1])

        return DetectedPattern(
            pattern_type=pattern_type,
            candle_count=candle_count,
            start_idx=int(chosen_start),
            end_idx=int(end_idx),
            pattern_start=pattern_start,
            pattern_end=pattern_end,
            last_price=last_close,
            avwap=avwap_end,
            distance_pct=distance_pct,
            avg_body_ratio=avg_body_ratio,
            max_body_ratio=max_body_ratio,
            window_range_pct=window_range_pct,
            touched=touched,
            anchor_timestamp=anchor_timestamp,
            latest_atr=latest_atr,
        )

    def _passes_close_filters(self, frame: pd.DataFrame, avwap_close: float) -> bool:
        """Check optional last-candle close-above filters.

        The last row is the most recent completed candle. Each enabled filter
        requires a strict close above its reference line.
        """
        config = self.config
        if not (config.require_close_above_avwap or config.require_close_above_vwap):
            return True

        close = float(pd.to_numeric(frame["close"], errors="coerce").iloc[-1])
        if not np.isfinite(close):
            return False

        if config.require_close_above_avwap and close <= avwap_close:
            return False

        if config.require_close_above_vwap:
            if "session_vwap" not in frame.columns:
                raise ValueError(
                    "Pattern detection needs a 'session_vwap' column when "
                    "require_close_above_vwap is enabled"
                )
            session_vwap = pd.to_numeric(frame["session_vwap"], errors="coerce").iloc[-1]
            if not np.isfinite(session_vwap) or close <= float(session_vwap):
                return False

        return True

    def _passes_below_filters(self, frame: pd.DataFrame) -> bool:
        """Check whether an earlier candle closed below AVWAP and/or VWAP.

        ``below_candle`` selects the candle: the one before the last completed
        candle, the one before that, or either. ``below_reference`` selects the
        line: AVWAP, session VWAP, or either. A missing candle means the filter
        cannot be satisfied.
        """

        config = self.config
        if config.below_reference == "off":
            return True

        offsets = {
            "previous": (-2,),
            "second_previous": (-3,),
            "either": (-2, -3),
        }[config.below_candle]
        if any(len(frame) + offset < 0 for offset in offsets):
            return False

        close = pd.to_numeric(frame["close"], errors="coerce")
        avwap = pd.to_numeric(frame["avwap"], errors="coerce")

        needs_vwap = config.below_reference in {"vwap", "either"}
        if needs_vwap and "session_vwap" not in frame.columns:
            raise ValueError(
                "Pattern detection needs a 'session_vwap' column when a "
                "session VWAP below filter is enabled"
            )
        vwap = (
            pd.to_numeric(frame["session_vwap"], errors="coerce")
            if needs_vwap
            else None
        )

        for offset in offsets:
            close_value = float(close.iloc[offset])
            if not np.isfinite(close_value):
                continue

            avwap_value = float(avwap.iloc[offset])
            below_avwap = (
                np.isfinite(avwap_value)
                and avwap_value > 0.0
                and close_value < avwap_value
            )

            below_vwap = False
            if vwap is not None:
                vwap_value = float(vwap.iloc[offset])
                below_vwap = (
                    np.isfinite(vwap_value)
                    and vwap_value > 0.0
                    and close_value < vwap_value
                )

            if config.below_reference == "avwap" and below_avwap:
                return True
            if config.below_reference == "vwap" and below_vwap:
                return True
            if config.below_reference == "either" and (below_avwap or below_vwap):
                return True

        return False

    def _classify(
        self,
        candle_count: int,
        window: pd.DataFrame,
        window_avwap: pd.Series,
        last_close: float,
        avwap_end: float,
        window_range_pct: float,
        touched: bool,
    ) -> str | None:
        config = self.config

        if candle_count >= 2:
            earlier_close = window["close"].iloc[:-1]
            earlier_avwap = window_avwap.iloc[:-1]
            was_below = bool((earlier_close < earlier_avwap).any())
            if was_below and last_close > avwap_end:
                return PATTERN_RECLAIM

        if (
            candle_count >= config.consolidation_min_candles
            and window_range_pct <= config.consolidation_range_pct
        ):
            return PATTERN_CONSOLIDATION

        if candle_count >= config.hugging_min_candles:
            return PATTERN_HUGGING

        if candle_count == 1 or touched:
            return PATTERN_TOUCH
        return PATTERN_HUGGING
