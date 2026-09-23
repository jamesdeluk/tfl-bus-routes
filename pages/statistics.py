"""Statistics page for weekday scheduled times and stop density."""

from urllib.error import HTTPError, URLError
import streamlit as st

from route_data import (
    active_data_source,
    get_offline_stats,
    get_offline_network_statistics,
    get_route_sequence,
    get_timetable,
    route_endpoints,
    route_length_km,
    network_min_median_max,
    parse_route_numbers,
    scheduled_time_summary,
    sequence_stops,
    stop_spacing_summary,
    stop_spacing_metres,
    weekday_segment_minutes,
    walking_minutes,
)


def summary_row(route_number: str, direction: str, source: str) -> dict[str, str | float | int]:
    """Calculate one route-direction row for the statistics table."""
    route_data = get_route_sequence(route_number, direction, source)
    stops = sequence_stops(route_data)
    timetable = get_timetable(route_number, stops[0]["id"], source)
    time_summary = scheduled_time_summary(timetable)
    distance_summary = stop_spacing_summary(route_data)
    start, end = route_endpoints(route_data)
    route_length = route_length_km(route_data)

    return {
        "Route": route_number,
        "Direction": f"{start} → {end}",
        "Stops": len(stops),
        "Route km": round(route_length, 2),
        "Stops / km": round(len(stops) / route_length, 2),
        "Min (min)": time_summary["min_minutes"],
        "Q25 (min)": time_summary["q25_minutes"],
        "Median (min)": time_summary["median_minutes"],
        "Q75 (min)": time_summary["q75_minutes"],
        "IQR (min)": time_summary["iqr_minutes"],
        "Max (min)": time_summary["max_minutes"],
        "Min spacing (m)": round(distance_summary["min_metres"]),
        "Q25 spacing (m)": round(distance_summary["q25_metres"]),
        "Median spacing (m)": round(distance_summary["median_metres"]),
        "Q75 spacing (m)": round(distance_summary["q75_metres"]),
        "IQR spacing (m)": round(distance_summary["iqr_metres"]),
        "Max spacing (m)": round(distance_summary["max_metres"]),
    }


def network_table_rows(statistics: dict[str, object]) -> list[dict[str, str | float]]:
    """Format genuine network-wide values as the compact summary table."""
    spacing = statistics["spacing_metres"]
    driving = statistics["driving_minutes"]
    walking = statistics["walking_minutes"]
    return [
        {"Metric": "Spacing (m)", "Min": spacing["min"], "Median": spacing["median"], "Max": spacing["max"]},
        {
            "Metric": "Driving time (min)",
            "Min": driving["min"],
            "Median": driving["median"],
            "Max": driving["max"],
        },
        {
            "Metric": "Walking time (min)",
            "Min": walking["min"],
            "Median": walking["median"],
            "Max": walking["max"],
        },
    ]


def live_network_statistics(route_numbers: tuple[str, ...]) -> dict[str, object]:
    """Calculate a true summary over every selected live-route stop pair and weekday interval."""
    spacing_values = []
    driving_values = []
    for route_number in route_numbers:
        for direction in ("outbound", "inbound"):
            route_data = get_route_sequence(route_number, direction, "api")
            stops = sequence_stops(route_data)
            if not stops:
                continue
            spacing_values.extend(stop_spacing_metres(route_data))
            driving_values.extend(
                weekday_segment_minutes(get_timetable(route_number, stops[0]["id"], "api"))
            )
    spacing = network_min_median_max(spacing_values)
    return {
        "spacing_metres": spacing,
        "driving_minutes": network_min_median_max(driving_values),
        "walking_minutes": {name: walking_minutes(value) for name, value in spacing.items()},
    }


def main() -> None:
    """Render the weekday scheduled-time and stop-density statistics table."""
    st.title("Statistics")
    source = active_data_source()
    st.caption("Weekday scheduled inter-stop times, stop spacing, and published stop density")
    api_routes = parse_route_numbers(st.session_state.get("api_route_input", ""))
    if source == "api" and not api_routes:
        st.info("Enter at least one route number in the Live API routes field.")
        return

    try:
        rows = (
            get_offline_stats()
            if source == "offline"
            else [
                summary_row(route_number, direction, source)
                for route_number in api_routes
                for direction in ("outbound", "inbound")
            ]
        )
        network_statistics = (
            get_offline_network_statistics()
            if source == "offline"
            else live_network_statistics(api_routes)
        )
    except (HTTPError, RuntimeError) as error:
        if isinstance(error, HTTPError) and error.code != 429:
            st.error("TfL timetable data could not be loaded. Please try again shortly.")
            st.exception(error)
        else:
            st.warning("TfL is temporarily rate-limiting live requests. Please try again shortly.")
        return
    except (URLError, TimeoutError, KeyError, ValueError) as error:
        st.error("TfL timetable data could not be loaded. Please try again shortly.")
        st.exception(error)
        return

    # Express each displayed spacing summary as an equivalent walking time at 4.5 km/h.
    for row in rows:
        for summary in ("Min", "Median", "Max"):
            row[f"{summary} walking time (min)"] = walking_minutes(
                float(row[f"{summary} spacing (m)"])
            )

    rows = [
        {
            column: (
                int(value)
                if column == "Stops" and isinstance(value, (int, float))
                else round(value, 1)
                if ("km" in column or "(min)" in column or "(m)" in column)
                and isinstance(value, (int, float))
                else value
            )
            for column, value in row.items()
        }
        for row in rows
    ]
    show_advanced = st.toggle("Show advanced distribution statistics", value=False)
    core_columns = [
        "Route",
        "Direction",
        "Stops",
        "Route km",
        "Stops / km",
        "Min (min)",
        "Median (min)",
        "Max (min)",
        "Min spacing (m)",
        "Median spacing (m)",
        "Max spacing (m)",
        "Min walking time (min)",
        "Median walking time (min)",
        "Max walking time (min)",
    ]
    advanced_columns = [
        "Q25 (min)",
        "Q75 (min)",
        "IQR (min)",
        "Q25 spacing (m)",
        "Q75 spacing (m)",
        "IQR spacing (m)",
    ]
    visible_columns = core_columns + advanced_columns if show_advanced else core_columns
    visible_rows = [{column: row[column] for column in visible_columns} for row in rows]
    st.subheader("Overall statistics")
    if source == "offline":
        st.caption(
            "Average route-direction stop density: "
            f"{network_statistics['average_stops_per_km']:.1f} stops/km "
            f"({network_statistics['average_stops_per_mile']:.1f} stops/mile)"
        )
    st.dataframe(
        [
            {
                column: round(value, 1) if isinstance(value, (int, float)) else value
                for column, value in row.items()
            }
            for row in network_table_rows(network_statistics)
        ],
        use_container_width=True,
        hide_index=True,
        height=144,
    )

    st.subheader("By route and direction")
    st.dataframe(
        visible_rows,
        use_container_width=True,
        hide_index=True,
        height=700,
    )
    st.info(
        "Overall statistics consider every consecutive stop pair and weekday timetable interval in "
        "the selected network. Times are minutes between successive scheduled stop times, weighted by weekday journeys. "
        "TfL publishes these offsets to whole minutes, so 0 means the two stops share a scheduled "
        "minute, not that travel takes no time. Spacing is straight-line distance between stop "
        "coordinates, so the road distance travelled will be longer. Stop density is published stops "
        "divided by the length of TfL's route geometry. Walking time converts each straight-line stop "
        "spacing using a 4.5 km/h city-walking pace."
    )


main()
