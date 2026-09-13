"""Map page for finding routes near one chosen location."""

from html import escape

import streamlit as st

from map_shared import (
    DEFAULT_ROUTE_NUMBERS,
    is_night_route,
    location_search_above_map,
    location_search_disclaimer,
    render_map,
)
from route_data import available_routes, parse_route_numbers, route_colour, routes_within_distance_metres


def main() -> None:
    """Render routes with a stop within a selected distance of one pin."""
    st.title("Explore")
    st.caption(
        "Click once to add a pin and show every route with a published stop within the chosen distance. "
        "Click the pin again to remove it."
    )
    source = st.session_state["data_source"]
    if "explore_pin" not in st.session_state:
        st.session_state["explore_pin"] = None
    pin = st.session_state["explore_pin"]
    routes = (
        available_routes(source)
        if source == "offline"
        else parse_route_numbers(st.session_state.get("api_route_input", ""))
    )
    search_target = location_search_above_map("explore")
    controls_column, map_column = st.columns([1, 4], gap="large")
    with controls_column:
        distance_metres = st.number_input(
            "Routes within (m)", min_value=50, value=400, step=50,
            help="Distance to a published bus stop, rather than distance to route geometry.",
        )
        only_night_routes = st.toggle("Only show night routes", value=False)
    candidate_routes = tuple(route for route in routes if is_night_route(route) == only_night_routes)
    displayed_routes = (
        routes_within_distance_metres(source, *pin["location"], float(distance_metres), candidate_routes)
        if pin
        else [route for route in DEFAULT_ROUTE_NUMBERS if route in candidate_routes]
    )
    if not displayed_routes and not pin:
        displayed_routes = list(candidate_routes[:9])
    route_colours = {route: route_colour(index) for index, route in enumerate(displayed_routes)}
    if pin:
        legend_items = "".join(
            # Use the same RGB values as the corresponding route geometry on the map.
            (
                '<span style="display:inline-flex;align-items:center;gap:0.35rem;margin:0 0.75rem 0.35rem 0;">'
                f'<span style="width:0.8rem;height:0.8rem;border-radius:50%;background:rgb({", ".join(map(str, colour))});"></span>'
                f"{escape(route)}</span>"
            )
            for route, colour in route_colours.items()
        )
        with controls_column:
            st.caption("Visible routes")
            st.markdown(legend_items, unsafe_allow_html=True)
    with map_column:
        if pin:
            st.caption(f"{len(displayed_routes)} routes within {distance_metres:.0f} m of the pin.")
        map_state = render_map(
            displayed_routes,
            source,
            [pin] if pin else [],
            route_colours,
            search_target,
            "explore-map",
        )
    location_search_disclaimer()
    clicked_tooltip = map_state.get("last_object_clicked_tooltip")
    if pin and clicked_tooltip == "Pin A — click to remove":
        st.session_state["explore_pin"] = None
        st.rerun()
    clicked_location = map_state.get("last_clicked")
    if clicked_location and clicked_tooltip != "Pin A — click to remove":
        st.session_state["explore_pin"] = {
            "label": "A",
            "location": (clicked_location["lat"], clicked_location["lng"]),
        }
        st.rerun()


main()
