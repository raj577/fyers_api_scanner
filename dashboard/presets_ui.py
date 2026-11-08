"""Sidebar controls for saving and loading scanner filter presets."""

from __future__ import annotations

import streamlit as st

from dashboard import theme
from scanner.models import PatternConfig
from scanner.presets import PresetError, PresetStore, ScannerPreset


def _apply_preset(
    store: PresetStore,
    name: str,
    timeframe_labels: dict[str, str],
) -> None:
    try:
        preset = store.get(name)
        if preset is None:
            raise PresetError(f"Preset {name!r} no longer exists.")
        _apply_preset_to_session(preset, timeframe_labels)
    except PresetError as exc:
        st.session_state["preset_error"] = str(exc)
        return
    st.session_state["preset_flash"] = f"Loaded scanner preset: {preset.name}"


def _apply_preset_to_session(
    preset: ScannerPreset,
    timeframe_labels: dict[str, str],
) -> None:
    resolution_to_label = {
        resolution: label for label, resolution in timeframe_labels.items()
    }
    if preset.timeframe not in resolution_to_label:
        raise PresetError(
            f"Preset uses an unsupported timeframe: {preset.timeframe!r}."
        )

    config = preset.config
    st.session_state["f_timeframe"] = resolution_to_label[preset.timeframe]
    st.session_state["f_min"] = config.min_candles
    st.session_state["f_max"] = config.max_candles
    st.session_state["f_above"] = config.max_close_above_pct * 100.0
    st.session_state["f_below"] = config.max_close_below_pct * 100.0
    st.session_state["f_low"] = config.max_low_below_pct * 100.0
    st.session_state["f_body"] = config.max_body_ratio
    st.session_state["f_range"] = config.max_window_range_pct * 100.0
    st.session_state["f_use_atr"] = config.max_candle_range_atr is not None
    st.session_state["f_atr"] = (
        config.max_candle_range_atr
        if config.max_candle_range_atr is not None
        else 2.0
    )
    st.session_state["f_cons_range"] = config.consolidation_range_pct * 100.0
    st.session_state["f_classes"] = list(config.allowed_patterns)
    st.session_state["f_close_above_avwap"] = config.require_close_above_avwap
    st.session_state["f_close_above_vwap"] = config.require_close_above_vwap
    st.session_state["f_below_ref"] = config.below_reference
    st.session_state["f_below_candle"] = config.below_candle


def _delete_preset(store: PresetStore, name: str) -> None:
    try:
        deleted = store.delete(name)
    except PresetError as exc:
        st.session_state["preset_error"] = str(exc)
        return
    st.session_state.pop("preset_selected", None)
    if deleted:
        st.session_state["preset_flash"] = f"Deleted scanner preset: {name}"
    else:
        st.session_state["preset_error"] = f"Preset {name!r} was not found."


def render_preset_manager(
    store: PresetStore,
    config: PatternConfig | None,
    config_error: str | None,
    resolution: str,
    timeframe_labels: dict[str, str],
) -> None:
    """Render save/load/delete controls after the pattern widgets exist."""

    theme.render_section_header(
        "Scanner presets",
        "Presets store the timeframe and pattern filters. "
        "Universe and contract selection are not included.",
    )

    try:
        presets = store.list()
    except PresetError as exc:
        st.error(str(exc))
        return

    names = [preset.name for preset in presets]
    selected = st.selectbox(
        "Saved preset",
        options=[""] + names,
        format_func=lambda name: name or "Choose a preset",
        key="preset_selected",
    )

    load_column, delete_column = st.columns(2)
    load_column.button(
        "Load preset",
        width="stretch",
        disabled=not selected,
        on_click=_apply_preset,
        args=(store, selected, timeframe_labels),
    )
    delete_column.button(
        "Delete preset",
        width="stretch",
        disabled=not selected,
        on_click=_delete_preset,
        args=(store, selected),
    )

    with st.expander("Save current filters as a preset", expanded=False):
        name = st.text_input(
            "Preset name",
            key="preset_name",
            placeholder="e.g. Tight 1-hour consolidation",
        )
        if st.button("Save preset", width="stretch"):
            if config is None:
                st.error(config_error or "Fix the pattern filters before saving.")
            elif not name.strip():
                st.error("Enter a preset name.")
            else:
                try:
                    preset = ScannerPreset.from_config(name, resolution, config)
                    store.save(preset)
                except PresetError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["preset_flash"] = (
                        f"Saved scanner preset: {preset.name}"
                    )
                    st.rerun()
