"""Map page for displaying explicitly selected TfL bus routes."""

import streamlit as st

from map_shared import location_search_above_map, location_search_disclaimer, render_map
from route_data import available_routes, parse_route_numbers, route_colour


def main() -> None:
    """Render selected route numbers without requiring a map pin."""
    st.title("Show")
    st.caption("Enter route numbers to display their published geometry and stops.")
    source = st.session_state["data_source"]
    search_target = location_search_above_map("show_route")
    controls_column, map_column = st.columns([1, 4], gap="large")
    with controls_column:
        route_text = st.text_input(
            "Route numbers",
            value="1 2 3 4 5",
            help="Enter route numbers separated by spaces, for example: 87 77 88.",
        )
    selected_routes = parse_route_numbers(route_text)
    valid_routes = set(available_routes(source)) if source == "offline" else set(selected_routes)
    displayed_routes = [route for route in selected_routes if route in valid_routes]
    unknown_routes = [route for route in selected_routes if route not in valid_routes]
    if unknown_routes:
        st.warning(f"Unavailable route numbers: {' · '.join(unknown_routes)}")
    if not displayed_routes:
        st.info("Enter at least one available route number.")
        return
    route_colours = {route: route_colour(index) for index, route in enumerate(displayed_routes)}
    with map_column:
        render_map(displayed_routes, source, [], route_colours, search_target, "show-route-map")
    location_search_disclaimer()


main()
