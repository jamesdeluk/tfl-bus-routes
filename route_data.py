"""Shared TfL data access and calculations for the route-explorer pages."""

import json
import math
import os
from pathlib import Path
import time
from collections import Counter
from itertools import product
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import streamlit as st


NEARBY_ROUTES = ("12", "159", "453", "24", "26", "87", "88", "91", "23", "9", "139")
PROJECT_DIRECTORY = Path(__file__).parent
# The app reads the compact map data; the full TfL download remains separately for reference.
OFFLINE_SNAPSHOT_PATH = PROJECT_DIRECTORY / "data" / "route_map_snapshot.json"
FULL_OFFLINE_SNAPSHOT_PATH = PROJECT_DIRECTORY / "data" / "route_snapshot.json"
OFFLINE_STATS_PATH = PROJECT_DIRECTORY / "data" / "route_stats.json"
OFFLINE_NETWORK_STATS_PATH = PROJECT_DIRECTORY / "data" / "network_statistics.json"
OFFLINE_LOCATION_INDEX_PATH = PROJECT_DIRECTORY / "data" / "london_location_index.json"
ROUTE_SEQUENCE_URL = "https://api.tfl.gov.uk/Line/{route}/Route/Sequence/{direction}"
TIMETABLE_URL = "https://api.tfl.gov.uk/Line/{route}/Timetable/{stop_id}"
EARTH_RADIUS_KM = 6_371.0088
WALKING_SPEED_KM_PER_HOUR = 4.5


def has_offline_data() -> bool:
    """Return whether every file required by the offline app mode is bundled."""
    return all(
        path.exists()
        for path in (OFFLINE_SNAPSHOT_PATH, OFFLINE_STATS_PATH, OFFLINE_NETWORK_STATS_PATH)
    )


def active_data_source() -> str:
    """Return the selected data source, including during direct page loads.

    Streamlit can execute a page directly from a deep link before the sidebar widget in app.py
    creates its session-state key. Defaulting here keeps every page usable in that fresh session.
    """
    default_source = "offline" if has_offline_data() else "api"
    return st.session_state.get("data_source", default_source)


class TflRateLimitError(RuntimeError):
    """Raised when TfL temporarily rejects requests because of rate limiting."""


@st.cache_data(ttl=60 * 60, show_spinner=False)
def get_route_sequence(
    route_number: str, direction: str, source: str = "api"
) -> dict[str, Any]:
    """Fetch and cache one current route direction from TfL's public API."""
    if source == "offline":
        return get_offline_snapshot()["routes"][route_number][direction]["sequence"]
    return fetch_json(ROUTE_SEQUENCE_URL.format(route=route_number, direction=direction))


@st.cache_data(ttl=60 * 60, show_spinner=False)
def get_timetable(route_number: str, stop_id: str, source: str = "api") -> dict[str, Any]:
    """Fetch and cache the timetable departing from a route's first stop."""
    if source == "offline":
        raise RuntimeError("Offline timetable responses are not bundled; use the precomputed statistics.")
    return fetch_json(TIMETABLE_URL.format(route=route_number, stop_id=stop_id))


@st.cache_resource(ttl=60, show_spinner=False)
def get_offline_snapshot() -> dict[str, Any]:
    """Load the compact, map-ready TfL route snapshot once per refresh interval."""
    with OFFLINE_SNAPSHOT_PATH.open() as snapshot_file:
        return json.load(snapshot_file)


@st.cache_data(ttl=60, show_spinner=False)
def get_offline_stats() -> list[dict[str, Any]]:
    """Load the precomputed statistics derived from the downloaded route snapshot."""
    with OFFLINE_STATS_PATH.open() as stats_file:
        return json.load(stats_file)


@st.cache_data(ttl=60, show_spinner=False)
def get_offline_network_statistics() -> dict[str, Any]:
    """Load the precomputed true network-wide summary from the full TfL snapshot."""
    with OFFLINE_NETWORK_STATS_PATH.open() as stats_file:
        return json.load(stats_file)


@st.cache_resource(show_spinner=False)
def get_offline_location_index() -> tuple[dict[str, Any], ...]:
    """Load the bundled Greater London place and street-name search index."""
    with OFFLINE_LOCATION_INDEX_PATH.open() as index_file:
        return tuple(json.load(index_file))


def normalise_location_query(value: str) -> str:
    """Normalise a place query without losing the words that make up a street name."""
    return " ".join("".join(character if character.isalnum() else " " for character in value.casefold()).split())


def location_label(location: dict[str, Any]) -> str:
    """Return a disambiguated label suitable for a location-search result."""
    context = " · ".join(value for value in (location["locality"], location["borough"]) if value)
    return f"{location['name']} — {context}" if context else location["name"]


def search_offline_locations(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Find bundled London areas and roads, favouring exact and prefix matches."""
    normalised_query = normalise_location_query(query)
    if len(normalised_query) < 2:
        return []

    query_words = normalised_query.split()
    matches = []
    for location in get_offline_location_index():
        normalised_name = normalise_location_query(location["name"])
        if not all(word in normalised_name for word in query_words):
            continue
        score = (
            0
            if normalised_name == normalised_query
            else 1
            if normalised_name.startswith(normalised_query)
            else 2
        )
        # A named area is normally more useful than a road when the text is ambiguous.
        location_kind_score = 0 if location["kind"] == "area" else 1
        central_london_distance = (location["lat"] - 51.5072) ** 2 + (location["lon"] + 0.1276) ** 2
        matches.append(
            (
                score,
                location_kind_score,
                len(normalised_name),
                central_london_distance,
                location_label(location),
                location,
            )
        )
    return [location for *_, location in sorted(matches)[:limit]]


def available_routes(source: str) -> tuple[str, ...]:
    """Return routes held in the selected source, with live mode kept intentionally small."""
    if source == "offline":
        return tuple(
            route_number
            for route_number, route_record in get_offline_snapshot()["routes"].items()
            if all(direction in route_record for direction in ("outbound", "inbound"))
        )
    return NEARBY_ROUTES


@st.cache_resource(ttl=60, show_spinner=False)
def offline_route_stop_index() -> dict[str, tuple[tuple[float, float], ...]]:
    """Index offline routes by immutable stop coordinates for fast nearest-route lookups."""
    return {
        route_number: tuple(
            (stop["lon"], stop["lat"])
            for direction in ("outbound", "inbound")
            for stop in sequence_stops(route_record.get(direction, {}).get("sequence", {"stopPointSequences": []}))
        )
        for route_number, route_record in get_offline_snapshot()["routes"].items()
    }


def parse_route_numbers(route_text: str) -> tuple[str, ...]:
    """Parse unique, space-separated route numbers entered for live API mode."""
    return tuple(dict.fromkeys(route_text.split()))


def route_colour(index: int) -> list[int]:
    """Generate an effectively unbounded sequence of distinct, high-contrast RGB colours."""
    initial_colours = (
        [255, 0, 0], [0, 255, 0], [0, 0, 255], [0, 255, 255], [255, 0, 255],
        [255, 255, 0], [64, 64, 255], [0, 128, 255], [0, 255, 128], [128, 0, 255],
        [255, 0, 128], [128, 255, 0], [255, 128, 0], [128, 128, 255], [128, 255, 128],
        [255, 128, 128], [128, 0, 128], [0, 128, 128], [128, 128, 0], [192, 192, 192],
    )
    if index < len(initial_colours):
        return initial_colours[index]

    remaining_index = index - len(initial_colours)
    divisions = 2
    while True:
        candidates = [
            [round(component * 255 / divisions) for component in components]
            for components in product(range(divisions + 1), repeat=3)
            if sum(component > 0 for component in components) >= 2
            and math.gcd(*components, divisions) == 1
            and [round(component * 255 / divisions) for component in components] not in initial_colours
        ]
        if remaining_index < len(candidates):
            return candidates[remaining_index]
        remaining_index -= len(candidates)
        divisions += 1


def nearest_routes(
    source: str,
    latitude: float,
    longitude: float,
    count: int = 5,
    route_numbers: tuple[str, ...] | None = None,
) -> list[str]:
    """Return routes whose published stops are closest to a selected map location."""
    distances = []
    candidate_routes = route_numbers or available_routes(source)
    if source == "offline":
        stop_index = offline_route_stop_index()
        for route_number in candidate_routes:
            stops = stop_index.get(route_number, ())
            if stops:
                nearest_distance = min(
                    haversine_km([longitude, latitude], coordinates) for coordinates in stops
                )
                distances.append((nearest_distance, route_number))
        return [route_number for _, route_number in sorted(distances)[:count]]

    for route_number in candidate_routes:
        stops = []
        for direction in ("outbound", "inbound"):
            try:
                stops.extend(sequence_stops(get_route_sequence(route_number, direction, source)))
            except (KeyError, HTTPError, TflRateLimitError):
                continue
        if stops:
            nearest_distance = min(
                haversine_km([longitude, latitude], [stop["lon"], stop["lat"]]) for stop in stops
            )
            distances.append((nearest_distance, route_number))
    return [route_number for _, route_number in sorted(distances)[:count]]


def routes_within_distance_metres(
    source: str,
    latitude: float,
    longitude: float,
    threshold_metres: float,
    route_numbers: tuple[str, ...] | None = None,
) -> list[str]:
    """Return routes with a published stop no further than the requested distance."""
    distances = []
    candidate_routes = route_numbers or available_routes(source)
    if source == "offline":
        stop_index = offline_route_stop_index()
        for route_number in candidate_routes:
            stops = stop_index.get(route_number, ())
            if stops:
                distance_metres = min(
                    haversine_km([longitude, latitude], coordinates) * 1_000
                    for coordinates in stops
                )
                if distance_metres <= threshold_metres:
                    distances.append((distance_metres, route_number))
        return [route_number for _, route_number in sorted(distances)]

    for route_number in candidate_routes:
        stops = []
        for direction in ("outbound", "inbound"):
            try:
                stops.extend(sequence_stops(get_route_sequence(route_number, direction, source)))
            except (KeyError, HTTPError, TflRateLimitError):
                continue
        if stops:
            distance_metres = min(
                haversine_km([longitude, latitude], [stop["lon"], stop["lat"]]) * 1_000
                for stop in stops
            )
            if distance_metres <= threshold_metres:
                distances.append((distance_metres, route_number))
    return [route_number for _, route_number in sorted(distances)]


def fetch_json(url: str) -> dict[str, Any]:
    """Request one TfL API response with a descriptive application user agent."""
    api_key = os.environ.get("TFL_API_KEY")
    if api_key:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}app_key={quote(api_key, safe='')}"
    request = Request(url, headers={"User-Agent": "tfl-bus-routes"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code != 429:
                raise
            retry_after = int(error.headers.get("Retry-After", 2**attempt))
            time.sleep(min(retry_after, 8))
    raise TflRateLimitError("TfL is temporarily limiting requests from this app.")


def sequence_stops(route_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ordered stops from all published route branches."""
    return [
        stop
        for sequence in route_data["stopPointSequences"]
        for stop in sequence["stopPoint"]
    ]


def route_endpoints(route_data: dict[str, Any]) -> tuple[str, str]:
    """Return the first and final stop names for a route direction."""
    stops = sequence_stops(route_data)
    return stops[0]["name"], stops[-1]["name"]


def route_paths(route_data: dict[str, Any], colour: list[int]) -> list[dict[str, Any]]:
    """Convert TfL's encoded route geometry into records for a PyDeck path layer."""
    return [{"path": polyline, "colour": colour} for polyline in route_polylines(route_data)]


def route_stops(
    route_data: dict[str, Any], route_number: str, colour: list[int]
) -> list[dict[str, Any]]:
    """Flatten TfL's ordered stop sequence into map-marker records."""
    return [
        {
            "name": stop["name"],
            "stop_id": stop["id"],
            "route": route_number,
            "direction": route_data["direction"],
            "sequence": index + 1,
            "coordinates": [stop["lon"], stop["lat"]],
            "colour": colour,
        }
        for index, stop in enumerate(sequence_stops(route_data))
    ]


def route_length_km(route_data: dict[str, Any]) -> float:
    """Calculate the length of published route geometry using great-circle distances."""
    return sum(polyline_length_km(polyline) for polyline in route_polylines(route_data))


def route_polylines(route_data: dict[str, Any]) -> list[list[list[float]]]:
    """Decode TfL's nested LineString values into individual coordinate polylines."""
    return [
        polyline
        for line_string in route_data["lineStrings"]
        for polyline in json.loads(line_string)
    ]


def polyline_length_km(points: list[list[float]]) -> float:
    """Sum the great-circle distance between each pair of adjacent geometry points."""
    return sum(haversine_km(start, end) for start, end in zip(points, points[1:]))


def haversine_km(start: list[float], end: list[float]) -> float:
    """Calculate the great-circle distance between two [longitude, latitude] points."""
    start_longitude, start_latitude = map(math.radians, start)
    end_longitude, end_latitude = map(math.radians, end)
    latitude_delta = end_latitude - start_latitude
    longitude_delta = end_longitude - start_longitude
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(start_latitude)
        * math.cos(end_latitude)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(haversine))


def walking_minutes(metres: float) -> float:
    """Convert a distance to walking time using a 4.5 km/h city-walking pace."""
    return metres / (WALKING_SPEED_KM_PER_HOUR * 1_000) * 60


def route_stop_coordinates(route_number: str, source: str) -> tuple[tuple[float, float], ...]:
    """Return published stop coordinates for one route from the active source."""
    if source == "offline":
        return offline_route_stop_index().get(route_number, ())
    return tuple(
        (stop["lon"], stop["lat"])
        for direction in ("outbound", "inbound")
        for stop in sequence_stops(get_route_sequence(route_number, direction, source))
    )


@st.cache_data(ttl=60, show_spinner=False)
def routes_share_stop(first_route: str, second_route: str, source: str) -> bool:
    """Return whether two routes use at least one identical published TfL bus stop."""
    if first_route == second_route:
        return False
    first_stop_ids = {
        stop["id"]
        for direction in ("outbound", "inbound")
        for stop in sequence_stops(get_route_sequence(first_route, direction, source))
    }
    second_stop_ids = {
        stop["id"]
        for direction in ("outbound", "inbound")
        for stop in sequence_stops(get_route_sequence(second_route, direction, source))
    }
    return bool(first_stop_ids.intersection(second_stop_ids))


@st.cache_data(ttl=60, show_spinner=False)
def closest_stop_distance_metres(
    first_route: str,
    second_route: str,
    source: str,
    threshold_metres: float = 100,
) -> float:
    """Return a close stop-pair distance, avoiding comparisons outside the threshold."""
    ordered_first_route, ordered_second_route = sorted((first_route, second_route))
    first_stops = route_stop_coordinates(ordered_first_route, source)
    second_stops = route_stop_coordinates(ordered_second_route, source)
    if not first_stops or not second_stops:
        return math.inf
    mean_latitude = sum(latitude for _, latitude in second_stops) / len(second_stops)
    latitude_cell_size = threshold_metres / 111_320
    longitude_cell_size = threshold_metres / (
        111_320 * max(math.cos(math.radians(mean_latitude)), 0.1)
    )
    stop_grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for longitude, latitude in second_stops:
        cell = (math.floor(latitude / latitude_cell_size), math.floor(longitude / longitude_cell_size))
        stop_grid.setdefault(cell, []).append((longitude, latitude))
    close_distances = [
        haversine_km(first_stop, second_stop) * 1_000
        for first_stop in first_stops
        for first_longitude, first_latitude in [first_stop]
        for latitude_offset in (-1, 0, 1)
        for longitude_offset in (-1, 0, 1)
        for second_stop in stop_grid.get(
            (
                math.floor(first_latitude / latitude_cell_size) + latitude_offset,
                math.floor(first_longitude / longitude_cell_size) + longitude_offset,
            ),
            [],
        )
        if haversine_km(first_stop, second_stop) * 1_000 <= threshold_metres
    ]
    return min(close_distances, default=math.inf)


def weekday_weight(schedule_name: str) -> int:
    """Return how many weekdays a published timetable schedule represents."""
    schedule_weights = {
        "Monday to Thursday": 4,
        "Friday": 1,
        "Monday to Friday": 5,
        "Monday-Friday": 5,
    }
    return schedule_weights.get(schedule_name, 0)


def weekday_segment_minutes(timetable_data: dict[str, Any]) -> list[float]:
    """Return weekday scheduled inter-stop minutes, weighted by published journeys."""
    segment_minutes = []
    for timetable_route in timetable_data["timetable"]["routes"]:
        journeys_by_interval = Counter()
        for schedule in timetable_route["schedules"]:
            day_weight = weekday_weight(schedule["name"])
            if day_weight == 0:
                continue
            for journey in schedule["knownJourneys"]:
                journeys_by_interval[str(journey["intervalId"])] += day_weight

        for interval in timetable_route["stationIntervals"]:
            journey_count = journeys_by_interval[interval["id"]]
            if journey_count == 0:
                continue
            stops = sorted(interval["intervals"], key=lambda stop: stop["timeToArrival"])
            for previous, current in zip(stops, stops[1:]):
                segment_minutes.extend(
                    [current["timeToArrival"] - previous["timeToArrival"]] * journey_count
                )
    return segment_minutes


def percentile(values: list[float], fraction: float) -> float:
    """Calculate a linearly interpolated percentile for an ordered numeric series."""
    ordered_values = sorted(values)
    position = (len(ordered_values) - 1) * fraction
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    lower_value = ordered_values[lower_index]
    upper_value = ordered_values[upper_index]
    return lower_value + (upper_value - lower_value) * (position - lower_index)


def scheduled_time_summary(timetable_data: dict[str, Any]) -> dict[str, float | int]:
    """Summarise weekday scheduled inter-stop times from a TfL timetable response."""
    values = weekday_segment_minutes(timetable_data)
    if not values:
        raise ValueError("The TfL timetable contains no recognised weekday journeys.")

    lower_quartile = percentile(values, 0.25)
    upper_quartile = percentile(values, 0.75)
    return {
        "min_minutes": min(values),
        "q25_minutes": lower_quartile,
        "median_minutes": percentile(values, 0.5),
        "q75_minutes": upper_quartile,
        "iqr_minutes": upper_quartile - lower_quartile,
        "max_minutes": max(values),
    }


def network_min_median_max(values: list[float]) -> dict[str, float]:
    """Summarise every value in a network, rather than averaging per-route summaries."""
    if not values:
        raise ValueError("No values were available for the network-wide summary.")
    return {"min": min(values), "median": percentile(values, 0.5), "max": max(values)}


def network_statistics(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Calculate true network-wide spacing and weekday scheduled-time statistics."""
    spacing_values: list[float] = []
    driving_values: list[float] = []
    stop_density_values: list[float] = []
    route_directions = 0
    for route_record in snapshot["routes"].values():
        for direction in ("outbound", "inbound"):
            sequence = route_record.get(direction, {}).get("sequence")
            if not sequence:
                continue
            stops = sequence_stops(sequence)
            if not stops:
                continue
            route_directions += 1
            spacing_values.extend(stop_spacing_metres(sequence))
            route_length = route_length_km(sequence)
            if route_length:
                stop_density_values.append(len(stops) / route_length)
            timetable = route_record.get("timetables", {}).get(stops[0]["id"])
            if timetable and "timetable" in timetable:
                driving_values.extend(weekday_segment_minutes(timetable))

    spacing = network_min_median_max(spacing_values)
    driving = network_min_median_max(driving_values)
    return {
        "routes": len(snapshot["routes"]),
        "route_directions": route_directions,
        "consecutive_stop_pairs": len(spacing_values),
        "weekday_scheduled_stop_pairs": len(driving_values),
        "average_stops_per_km": sum(stop_density_values) / len(stop_density_values),
        "average_stops_per_mile": sum(stop_density_values) / len(stop_density_values) * 1.609344,
        "spacing_metres": spacing,
        "driving_minutes": driving,
        "walking_minutes": {name: walking_minutes(value) for name, value in spacing.items()},
    }


def stop_spacing_metres(route_data: dict[str, Any]) -> list[float]:
    """Calculate straight-line metres between successive published stop coordinates."""
    return [
        haversine_km(
            [previous["lon"], previous["lat"]],
            [current["lon"], current["lat"]],
        )
        * 1_000
        for stop_sequence in route_data["stopPointSequences"]
        for previous, current in zip(stop_sequence["stopPoint"], stop_sequence["stopPoint"][1:])
    ]


@st.cache_data(ttl=60, show_spinner=False)
def route_direction_stop_spacing_metres(
    route_number: str, direction: str, source: str
) -> list[float]:
    """Return cached consecutive-stop distances for one route direction."""
    return stop_spacing_metres(get_route_sequence(route_number, direction, source))


@st.cache_data(ttl=60, show_spinner=False)
def all_offline_stop_spacing_metres() -> list[float]:
    """Return every consecutive-stop spacing from the downloaded route directions."""
    values = []
    for route_record in get_offline_snapshot()["routes"].values():
        for direction in ("outbound", "inbound"):
            route_data = route_record.get(direction, {}).get("sequence")
            if route_data:
                values.extend(stop_spacing_metres(route_data))
    return values


def stop_spacing_summary(route_data: dict[str, Any]) -> dict[str, float | int]:
    """Summarise the straight-line spacing between consecutive stops."""
    values = stop_spacing_metres(route_data)
    if not values:
        raise ValueError("The TfL route contains fewer than two stops.")

    lower_quartile = percentile(values, 0.25)
    upper_quartile = percentile(values, 0.75)
    return {
        "min_metres": min(values),
        "q25_metres": lower_quartile,
        "median_metres": percentile(values, 0.5),
        "q75_metres": upper_quartile,
        "iqr_metres": upper_quartile - lower_quartile,
        "max_metres": max(values),
    }
