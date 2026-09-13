"""Statistics page for weekday scheduled times and stop density."""

from urllib.error import HTTPError, URLError
from statistics import fmean

import streamlit as st

from route_data import (
    get_offline_stats,
    get_route_sequence,
    get_timetable,
    route_endpoints,
    route_length_km,
    parse_route_numbers,
    scheduled_time_summary,
    sequence_stops,
    stop_spacing_summary,
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


def overall_mean_row(rows: list[dict[str, str | float | int]]) -> dict[str, str | float | int]:
    """Calculate the arithmetic mean of every numeric statistics column."""
    first_row = rows[0]
    mean_row = {
        "Route": "Overall mean",
        "Direction": f"{len(rows)} route-directions",
        "Routes": len({str(row["Route"]) for row in rows}),
    }
    for column, value in first_row.items():
        if isinstance(value, (int, float)):
            mean_value = fmean(row[column] for row in rows)
            if column == "Stops":
                mean_row[column] = round(mean_value)
            elif "km" in column or "(min)" in column or "(m)" in column:
                mean_row[column] = round(mean_value, 1)
            else:
                mean_row[column] = mean_value
    return mean_row


def main() -> None:
    """Render the weekday scheduled-time and stop-density statistics table."""
    st.title("Statistics")
    source = st.session_state["data_source"]
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
    overall_row = overall_mean_row(rows)
    overall_columns = ["Routes"] + [
        column for column in visible_columns if column not in ("Route", "Direction")
    ]
    st.dataframe(
        [{column: overall_row[column] for column in overall_columns}],
        use_container_width=True,
        hide_index=True,
        height=74,
    )

    st.subheader("By route and direction")
    st.dataframe(
        visible_rows,
        use_container_width=True,
        hide_index=True,
        height=700,
    )
    st.info(
        "Times are minutes between successive scheduled stop times, weighted by weekday journeys. "
        "TfL publishes these offsets to whole minutes, so 0 means the two stops share a scheduled "
        "minute, not that travel takes no time. Spacing is straight-line distance between stop "
        "coordinates, so the road distance travelled will be longer. Stop density is published stops "
        "divided by the length of TfL's route geometry. Walking time converts each straight-line stop "
        "spacing using a 4.5 km/h city-walking pace."
    )


main()
