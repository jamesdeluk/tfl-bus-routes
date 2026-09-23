"""Distribution of consecutive-stop spacing across the selected bus routes."""

from collections import Counter
import math

import altair as alt
import streamlit as st

from route_data import (
    active_data_source,
    available_routes,
    parse_route_numbers,
    route_direction_stop_spacing_metres,
    walking_minutes,
)


def spacing_values(source: str, route_numbers: tuple[str, ...]) -> list[float]:
    """Load every consecutive-stop spacing for the selected routes."""
    return [
        spacing
        for route_number in route_numbers
        for direction in ("outbound", "inbound")
        for spacing in route_direction_stop_spacing_metres(route_number, direction, source)
    ]


def histogram(
    values: list[float], bin_width: float, show_percentage: bool, show_walking_time: bool
) -> alt.Chart:
    """Build a histogram whose axes can display distance or equivalent walking time."""
    if show_walking_time:
        values = [walking_minutes(value) for value in values]
    counts = Counter(math.floor(value / bin_width) * bin_width for value in values)
    records = [
        {
            "bin_start": bin_start,
            "bin_end": bin_start + bin_width,
            "baseline": 0,
            "count": count,
            "percentage": count / len(values) * 100,
        }
        for bin_start, count in sorted(counts.items())
    ]
    y_field = "percentage:Q" if show_percentage else "count:Q"
    y_title = "Stop pairs (%)" if show_percentage else "Number of route-direction stop pairs"
    y_tooltip = (
        alt.Tooltip("percentage:Q", title="Stop pairs", format=".1f")
        if show_percentage
        else alt.Tooltip("count:Q", title="Stop pairs")
    )
    x_title = (
        "Walking time between consecutive stops (min)"
        if show_walking_time
        else "Straight-line spacing between consecutive stops (m)"
    )
    x_format = ".1f" if show_walking_time else ".0f"
    return (
        alt.Chart({"values": records})
        .mark_bar()
        .encode(
            x=alt.X("bin_start:Q", title=x_title),
            x2="bin_end:Q",
            # Keep the baseline at zero instead of allowing Vega-Lite to pad below it.
            y=alt.Y(y_field, title=y_title, scale=alt.Scale(domainMin=0)),
            # A ranged x-axis needs an explicit y2 for Vega-Lite to draw filled columns.
            y2=alt.Y2("baseline:Q"),
            tooltip=[
                alt.Tooltip("bin_start:Q", title="From", format=x_format),
                alt.Tooltip("bin_end:Q", title="To", format=x_format),
                y_tooltip,
            ],
        )
        .properties(height=500)
    )


def main() -> None:
    """Render the all-route stop-spacing histogram."""
    st.title("Spacing")
    source = active_data_source()
    st.caption(
        "Every consecutive published stop pair across route directions. "
        "Shared road sections appear once for each service and direction that uses them."
    )
    available_route_numbers = (
        available_routes(source)
        if source == "offline"
        else parse_route_numbers(st.session_state.get("api_route_input", ""))
    )
    selection_key = "spacing_selected_routes"
    selected_routes = st.session_state.get(selection_key, list(available_route_numbers))
    selected_routes = [route for route in selected_routes if route in available_route_numbers]
    if selection_key not in st.session_state or selected_routes != st.session_state[selection_key]:
        st.session_state[selection_key] = selected_routes
    all_column, none_column = st.columns(2)
    with all_column:
        if st.button("All", use_container_width=True):
            st.session_state[selection_key] = list(available_route_numbers)
            st.rerun()
    with none_column:
        if st.button("None", use_container_width=True):
            st.session_state[selection_key] = []
            st.rerun()
    selected_routes = st.multiselect(
        "Routes",
        options=available_route_numbers,
        key=selection_key,
    )
    show_walking_time = st.toggle("Show walking time", value=False)
    bin_width = (
        st.number_input(
            "Histogram bin width (min)", min_value=0.1, value=0.5, step=0.1, key="walking_bin_width"
        )
        if show_walking_time
        else st.number_input("Histogram bin width (m)", min_value=10, value=25, step=5, key="spacing_bin_width")
    )
    show_percentage = st.toggle("Show percentage", value=False)

    try:
        values = spacing_values(source, tuple(selected_routes))
    except (KeyError, ValueError) as error:
        st.error("TfL route geometry could not be loaded.")
        st.exception(error)
        return

    if not values:
        st.info("There are no consecutive-stop distances in the selected data.")
        return

    st.caption(f"{len(values):,} stop pairs; median spacing {sorted(values)[len(values) // 2]:.0f} m")
    st.altair_chart(
        histogram(values, float(bin_width), show_percentage, show_walking_time), use_container_width=True
    )


main()
