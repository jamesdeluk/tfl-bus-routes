"""Map page for comparing routes around two locations."""

from html import escape

import streamlit as st

from map_shared import (
    DEFAULT_ROUTE_NUMBERS,
    PIN_COLOURS,
    is_night_route,
    location_search_above_map,
    location_search_disclaimer,
    render_map,
)
from route_data import (
    active_data_source,
    available_routes,
    closest_stop_distance_metres,
    nearest_routes,
    parse_route_numbers,
    route_colour,
    routes_share_stop,
)


def main() -> None:
    """Show routes shared by two pin-based route sets, with optional broader overlap analysis."""
    st.title("Go")
    st.caption(
        "Click to add Pin A and Pin B. Click a pin to remove it. "
        "By default, only routes selected for both pins are shown."
    )
    source = active_data_source()
    if "go_pins" not in st.session_state:
        st.session_state["go_pins"] = []
    pins = st.session_state["go_pins"][:2]
    routes = (
        available_routes(source)
        if source == "offline"
        else parse_route_numbers(st.session_state.get("api_route_input", ""))
    )
    search_target = location_search_above_map("go")
    controls_column, map_column = st.columns([1, 4], gap="large")
    with controls_column:
        route_count = st.number_input("Nearest routes per pin", min_value=1, value=9, step=1)
        only_night_routes = st.toggle("Only show night routes", value=False)
        only_shared_routes = st.toggle("Only show shared routes", value=True)
        show_proximity_overlaps = st.toggle("Include stop-proximity overlaps", value=False)
        if show_proximity_overlaps:
            proximity_threshold = st.number_input("Stop proximity threshold (m)", min_value=25, value=100, step=25)
            exact_stop_overlaps_only = st.toggle("Exact overlaps only", value=False)
        else:
            proximity_threshold = 100
            exact_stop_overlaps_only = False
        if pins and st.button("Clear pins"):
            st.session_state["go_pins"] = []
            st.rerun()
    candidate_routes = tuple(route for route in routes if is_night_route(route) == only_night_routes)
    pin_routes = {
        str(pin["label"]): nearest_routes(source, *pin["location"], route_count, candidate_routes)
        for pin in pins
    }
    shared_routes = sorted(set(pin_routes.get("A", [])).intersection(pin_routes.get("B", [])))
    proximity_pairs = (
        [
            (first_route, second_route)
            for first_route in pin_routes.get("A", [])
            for second_route in pin_routes.get("B", [])
            if first_route != second_route
            if closest_stop_distance_metres(first_route, second_route, source, float(proximity_threshold))
            <= proximity_threshold
        ]
        if show_proximity_overlaps and {"A", "B"}.issubset(pin_routes)
        else []
    )
    proximity_routes = {
        route for first_route, second_route in proximity_pairs for route in (first_route, second_route)
    }
    shared_stop_pairs = (
        {
            tuple(sorted((first_route, second_route)))
            for first_route in pin_routes.get("A", [])
            for second_route in pin_routes.get("B", [])
            if first_route != second_route and routes_share_stop(first_route, second_route, source)
        }
        if exact_stop_overlaps_only
        else set()
    )
    shared_stop_routes = {
        route for first_route, second_route in shared_stop_pairs for route in (first_route, second_route)
    }
    all_pin_routes = list(dict.fromkeys(route for routes_for_pin in pin_routes.values() for route in routes_for_pin))
    # Do not suggest routes until both locations have been chosen.
    displayed_routes = all_pin_routes if len(pins) == 2 else []
    if only_shared_routes and {"A", "B"}.issubset(pin_routes):
        displayed_routes = [route for route in displayed_routes if route in shared_routes]
    if show_proximity_overlaps:
        displayed_routes = [route for route in displayed_routes if route in set(shared_routes).union(proximity_routes)]
    if exact_stop_overlaps_only:
        displayed_routes = [route for route in displayed_routes if route in shared_stop_routes]
    route_colours = {
        route: (
            [128, 0, 128]
            if route in shared_routes
            else route_colour(index)
        )
        for index, route in enumerate(displayed_routes)
    }
    with map_column:
        if len(pins) == 2:
            st.caption(f"Shared routes: {' · '.join(shared_routes) if shared_routes else 'none'}")
        map_state = render_map(displayed_routes, source, pins, route_colours, search_target, "go-map")
    with controls_column:
        if displayed_routes:
            legend_items = "".join(
                # Use the same RGB values as the corresponding route geometry on the map.
                (
                    '<span style="display:inline-flex;align-items:center;gap:0.35rem;margin:0 0.75rem 0.35rem 0;">'
                    f'<span style="width:0.8rem;height:0.8rem;border-radius:50%;background:rgb({", ".join(map(str, colour))});"></span>'
                    f"{escape(route)}</span>"
                )
                for route, colour in route_colours.items()
            )
            st.caption("Visible routes")
            st.markdown(legend_items, unsafe_allow_html=True)
        for label, routes_for_pin in pin_routes.items():
            st.markdown(
                f'<span style="display:inline-flex;align-items:center;gap:0.35rem;font-weight:600;">'
                f'<span style="width:0.8rem;height:0.8rem;border-radius:50%;background:{PIN_COLOURS[label]};"></span>'
                f"Pin {escape(label)}</span>",
                unsafe_allow_html=True,
            )
            st.write(" · ".join(routes_for_pin))
    location_search_disclaimer()
    clicked_tooltip = map_state.get("last_object_clicked_tooltip")
    pin_to_remove = next(
        (pin for pin in pins if clicked_tooltip == f"Pin {pin['label']} — click to remove"),
        None,
    )
    if pin_to_remove:
        st.session_state["go_pins"] = [pin for pin in pins if pin != pin_to_remove]
        st.rerun()
    clicked_location = map_state.get("last_clicked")
    if clicked_location and not pin_to_remove and len(pins) < 2:
        label = ("A", "B")[len(pins)]
        st.session_state["go_pins"] = [
            *pins,
            {"label": label, "location": (clicked_location["lat"], clicked_location["lng"])},
        ]
        st.rerun()


main()
