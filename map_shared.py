"""Shared map rendering and offline location search controls for route-use-case pages."""

from typing import Any

import folium
import streamlit as st
from streamlit_folium import st_folium

from route_data import (
    get_route_sequence,
    location_label,
    route_polylines,
    search_offline_locations,
    sequence_stops,
)


DEFAULT_ROUTE_NUMBERS = ("1", "2", "3", "4", "5", "6", "7", "8", "9")
PIN_COLOURS = {"A": "#FF0000", "B": "#0000FF"}


def is_night_route(route_number: str) -> bool:
    """Identify TfL night routes by their N-prefixed public route number."""
    return route_number.upper().startswith("N")


def location_search_above_map(state_prefix: str) -> dict[str, Any] | None:
    """Render a full-width offline place search and return its temporary map target."""
    result_key = f"{state_prefix}_location_results"
    target_key = f"{state_prefix}_search_target"
    result_index_key = f"{state_prefix}_location_result_index"
    with st.container(border=True):
        search_column, results_column = st.columns([4, 5], gap="medium")
        with search_column:
            with st.form(f"{state_prefix}-location-search-form", border=False):
                input_column, find_column = st.columns([3, 1])
                with input_column:
                    query = st.text_input(
                        "Location search",
                        placeholder="e.g. Clapham",
                        label_visibility="collapsed",
                        key=f"{state_prefix}_location_query",
                    )
                with find_column:
                    submitted = st.form_submit_button("Find", use_container_width=True)
            if submitted:
                results = search_offline_locations(query)
                st.session_state[result_key] = results
                st.session_state[target_key] = results[0] if results else None
                st.session_state[result_index_key] = 0
                if not results:
                    st.info("No matching London area or street was found.")
        with results_column:
            results = st.session_state.get(result_key, [])
            target = st.session_state.get(target_key)
            if results:
                select_column, show_column, clear_column = st.columns([4, 1, 1])
                with select_column:
                    selected_index = st.selectbox(
                        "Matches",
                        options=range(len(results)),
                        format_func=lambda index: location_label(results[index]),
                        label_visibility="collapsed",
                        key=result_index_key,
                    )
                with show_column:
                    show_selected = st.button("Show", key=f"{state_prefix}_show_location", use_container_width=True)
                with clear_column:
                    clear_target = st.button(
                        "Clear",
                        key=f"{state_prefix}_clear_location",
                        use_container_width=True,
                        disabled=target is None,
                    )
                if show_selected:
                    st.session_state[target_key] = results[selected_index]
                    st.rerun()
                if clear_target:
                    del st.session_state[target_key]
                    st.rerun()
    return st.session_state.get(target_key)


def location_search_disclaimer() -> None:
    """Show the OS Open Names attribution beneath a map page."""
    st.caption("Offline search data: OS Open Names © Crown copyright and database right 2026.")


def map_centre(route_numbers: list[str], source: str) -> tuple[float, float]:
    """Find a useful map centre from the selected routes' published stops."""
    stops = [
        stop
        for route_number in route_numbers
        for direction in ("outbound", "inbound")
        for stop in sequence_stops(get_route_sequence(route_number, direction, source))
    ]
    # The Go page begins without routes, so retain a useful map before pins are added.
    if not stops:
        return 51.5074, -0.1278
    return (
        sum(stop["lat"] for stop in stops) / len(stops),
        sum(stop["lon"] for stop in stops) / len(stops),
    )


def build_map(
    route_numbers: list[str],
    source: str,
    pins: list[dict[str, Any]],
    route_colours: dict[str, list[int]],
    search_target: dict[str, Any] | None,
) -> folium.Map:
    """Draw route geometry, a temporary search target, and up to two removable pins."""
    latitude, longitude = (
        (search_target["lat"], search_target["lon"])
        if search_target
        else (
            (
                sum(pin["location"][0] for pin in pins) / len(pins),
                sum(pin["location"][1] for pin in pins) / len(pins),
            )
            if pins
            else map_centre(route_numbers, source)
        )
    )
    route_map = folium.Map(
        location=[latitude, longitude],
        zoom_start=15 if search_target else 13,
        tiles="CartoDB positron",
    )
    for route_number in route_numbers:
        colour = "#{:02x}{:02x}{:02x}".format(*route_colours[route_number])
        for direction in ("outbound", "inbound"):
            route_data = get_route_sequence(route_number, direction, source)
            for polyline in route_polylines(route_data):
                folium.PolyLine(
                    [(point[1], point[0]) for point in polyline],
                    color=colour,
                    weight=4,
                    opacity=0.5,
                ).add_to(route_map)
            for stop in sequence_stops(route_data):
                folium.CircleMarker(
                    [stop["lat"], stop["lon"]],
                    radius=3,
                    color=colour,
                    opacity=0.5,
                    fill=True,
                    fill_opacity=0.5,
                    tooltip=f"Route {route_number}: {stop['name']}",
                ).add_to(route_map)
    if search_target:
        folium.Marker(
            [search_target["lat"], search_target["lon"]],
            tooltip=f"Search target: {location_label(search_target)}",
            icon=folium.Icon(color="orange", icon="crosshairs", prefix="fa"),
        ).add_to(route_map)
    for pin in pins:
        label = str(pin["label"])
        folium.CircleMarker(
            pin["location"],
            radius=8,
            color="#111827",
            weight=2,
            fill=True,
            fill_color=PIN_COLOURS[label],
            fill_opacity=1,
            tooltip=f"Pin {label} — click to remove",
        ).add_to(route_map)
    return route_map


def render_map(
    route_numbers: list[str],
    source: str,
    pins: list[dict[str, Any]],
    route_colours: dict[str, list[int]],
    search_target: dict[str, Any] | None,
    map_key: str,
) -> dict[str, Any]:
    """Render the common Folium map and return Streamlit-Folium click state."""
    return st_folium(
        build_map(route_numbers, source, pins, route_colours, search_target),
        height=700,
        use_container_width=True,
        key=map_key,
        # Panning and zooming remain browser-only; only interactions used by the pages rerun Streamlit.
        returned_objects=["last_clicked", "last_object_clicked_tooltip"],
    )
