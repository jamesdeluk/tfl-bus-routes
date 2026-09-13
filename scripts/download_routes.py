"""Download TfL's current bus-route data and precompute weekday statistics."""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError

# Scripts live one level below the application and its bundled data directory.
PROJECT_DIRECTORY = Path(__file__).parent.parent
# Allow both `uv run scripts/download_routes.py` and module execution from the project root.
if str(PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIRECTORY))

from route_data import (
    ROUTE_SEQUENCE_URL,
    TIMETABLE_URL,
    TflRateLimitError,
    fetch_json,
    route_endpoints,
    route_length_km,
    scheduled_time_summary,
    sequence_stops,
    stop_spacing_summary,
)


DATA_DIRECTORY = PROJECT_DIRECTORY / "data"
FULL_SNAPSHOT_PATH = DATA_DIRECTORY / "route_snapshot.json"
COMPACT_SNAPSHOT_PATH = DATA_DIRECTORY / "route_map_snapshot.json"
STATS_PATH = DATA_DIRECTORY / "route_stats.json"
IN_PROGRESS_SNAPSHOT_PATH = DATA_DIRECTORY / "route_snapshot.in_progress.json"
IN_PROGRESS_STATS_PATH = DATA_DIRECTORY / "route_stats.in_progress.json"
# Target 480 requests per minute, leaving a small margin below TfL's keyed limit.
REQUEST_INTERVAL_SECONDS = 0.125
WORKER_COUNT = 32
# Keep the download resumable without rewriting an increasingly large JSON file per route.
CHECKPOINT_INTERVAL_ROUTES = 16
VERBOSE_REQUESTS = "--verbose" in sys.argv
request_limiter_lock = Lock()
next_request_time = 0.0


def wait_for_request_slot() -> None:
    """Space all concurrent requests below the shared TfL rate target."""
    global next_request_time
    with request_limiter_lock:
        now = time.monotonic()
        wait_time = max(0.0, next_request_time - now)
        next_request_time = max(now, next_request_time) + REQUEST_INTERVAL_SECONDS
    if wait_time:
        time.sleep(wait_time)


def fetch_with_retry(url: str, label: str) -> dict:
    """Request a TfL endpoint within the shared limit and retry transient failures."""
    for attempt in range(3):
        try:
            wait_for_request_slot()
            started_at = time.perf_counter()
            if VERBOSE_REQUESTS:
                print(f"→ {label} (attempt {attempt + 1})", flush=True)
            response = fetch_json(url)
            if VERBOSE_REQUESTS:
                elapsed = time.perf_counter() - started_at
                print(f"← {label} received in {elapsed:.2f}s", flush=True)
            return response
        except (HTTPError, URLError, TimeoutError, TflRateLimitError) as error:
            if VERBOSE_REQUESTS:
                print(f"! {label} failed: {error}", flush=True)
            if attempt == 2:
                raise error
            time.sleep(30 if isinstance(error, TflRateLimitError) else 2**attempt)
    raise RuntimeError("Unreachable retry state")


def make_stats_row(route_number: str, direction: str, sequence: dict, timetable: dict) -> dict:
    """Create one offline route-direction statistics row."""
    stops = sequence_stops(sequence)
    start, end = route_endpoints(sequence)
    time_summary = scheduled_time_summary(timetable)
    distance_summary = stop_spacing_summary(sequence)
    route_length = route_length_km(sequence)
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


def compact_sequence(sequence: dict) -> dict:
    """Keep only the route geometry and stop fields used by the bundled app."""
    return {
        "direction": sequence["direction"],
        "lineStrings": sequence["lineStrings"],
        "stopPointSequences": [
            {
                "stopPoint": [
                    {field: stop[field] for field in ("id", "name", "lat", "lon")}
                    for stop in stop_sequence["stopPoint"]
                ]
            }
            for stop_sequence in sequence["stopPointSequences"]
        ],
    }


def compact_snapshot(snapshot: dict) -> dict:
    """Drop raw timetable responses and TfL fields that are unused by the deployed app."""
    return {
        "downloaded_at": snapshot["downloaded_at"],
        "routes": {
            route_number: {
                direction: {"sequence": compact_sequence(record[direction]["sequence"])}
                for direction in ("outbound", "inbound")
                if record.get(direction, {}).get("sequence")
            }
            for route_number, record in snapshot["routes"].items()
        },
        "failures": snapshot["failures"],
        "requested_routes": snapshot["requested_routes"],
    }


def write_json(path: Path, data: object) -> None:
    """Atomically replace a JSON checkpoint so an interruption cannot corrupt it."""
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w") as output_file:
        json.dump(data, output_file, separators=(",", ":"))
        output_file.flush()
        os.fsync(output_file.fileno())
    temporary_path.replace(path)


def read_json(path: Path, default: object) -> tuple[object, bool]:
    """Read a checkpoint, retaining a malformed copy rather than crashing."""
    if not path.exists():
        return default, False
    try:
        with path.open() as input_file:
            return json.load(input_file), False
    except json.JSONDecodeError:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        corrupt_path = path.with_name(f"{path.stem}.corrupt-{timestamp}{path.suffix}")
        path.replace(corrupt_path)
        print(f"Malformed checkpoint retained as {corrupt_path.name}; starting it again.", flush=True)
        return default, True


def route_is_complete(route_record: dict) -> bool:
    """Check whether both route directions and their timetables were downloaded."""
    return all(
        direction in route_record
        and "sequence" in route_record[direction]
        and route_record[direction]["sequence"]["stopPointSequences"]
        and route_record["timetables"]
        for direction in ("outbound", "inbound")
    )


def download_route(route_number: str) -> tuple[str, dict, list[dict], list[dict]]:
    """Download both directions of one route and return data for main-thread checkpointing."""
    route_record = {"outbound": {}, "inbound": {}, "timetables": {}}
    statistics = []
    failures = []
    for direction in ("outbound", "inbound"):
        try:
            sequence = fetch_with_retry(
                ROUTE_SEQUENCE_URL.format(route=route_number, direction=direction),
                f"{route_number} {direction} sequence",
            )
            route_record[direction]["sequence"] = sequence
            stops = sequence_stops(sequence)
            if not stops:
                raise ValueError("TfL returned a route sequence with no stops.")
            timetable = fetch_with_retry(
                TIMETABLE_URL.format(route=route_number, stop_id=stops[0]["id"]),
                f"{route_number} {direction} timetable",
            )
            route_record["timetables"][stops[0]["id"]] = timetable
            statistics.append(make_stats_row(route_number, direction, sequence, timetable))
        except (HTTPError, URLError, TimeoutError, TflRateLimitError, KeyError, ValueError) as error:
            failures.append({"route": route_number, "direction": direction, "error": str(error)})
    return route_number, route_record, statistics, failures


def main() -> None:
    """Download all current TfL bus routes, then write the snapshot and statistics files."""
    DATA_DIRECTORY.mkdir(exist_ok=True)
    lines = fetch_with_retry("https://api.tfl.gov.uk/Line/Mode/bus", "bus route catalogue")
    route_numbers = tuple(sorted(line["id"] for line in lines))
    initial_snapshot = {
        "downloaded_at": datetime.now(UTC).isoformat(),
        "routes": {},
        "failures": [],
        "requested_routes": route_numbers,
    }
    snapshot, snapshot_was_corrupt = read_json(
        IN_PROGRESS_SNAPSHOT_PATH,
        initial_snapshot,
    )
    statistics, statistics_were_corrupt = read_json(IN_PROGRESS_STATS_PATH, [])
    if snapshot_was_corrupt or statistics_were_corrupt:
        # The two checkpoints must describe the same download, so restart both together.
        snapshot = initial_snapshot
        statistics = []
        write_json(IN_PROGRESS_SNAPSHOT_PATH, snapshot)
        write_json(IN_PROGRESS_STATS_PATH, statistics)
    completed_routes = {route for route, record in snapshot["routes"].items() if route_is_complete(record)}

    pending_routes = [route for route in route_numbers if route not in completed_routes]
    with ThreadPoolExecutor(max_workers=WORKER_COUNT) as executor:
        futures = {executor.submit(download_route, route): route for route in pending_routes}
        for completed_count, future in enumerate(as_completed(futures), start=1):
            route_number, route_record, route_statistics, failures = future.result()
            print(f"{completed_count}/{len(pending_routes)} completed: {route_number}", flush=True)
            if route_record["outbound"] or route_record["inbound"]:
                snapshot["routes"][route_number] = route_record
            statistics.extend(route_statistics)
            snapshot["failures"].extend(failures)
            if completed_count % CHECKPOINT_INTERVAL_ROUTES == 0:
                write_json(IN_PROGRESS_SNAPSHOT_PATH, snapshot)
                write_json(IN_PROGRESS_STATS_PATH, statistics)
                print(f"Checkpoint saved after {completed_count} routes.", flush=True)

    snapshot["downloaded_at"] = datetime.now(UTC).isoformat()
    # Persist the final partial batch before publishing the completed snapshot.
    write_json(IN_PROGRESS_SNAPSHOT_PATH, snapshot)
    write_json(IN_PROGRESS_STATS_PATH, statistics)
    # Retain the full download for reference and publish a lightweight snapshot for the app.
    write_json(FULL_SNAPSHOT_PATH, snapshot)
    write_json(COMPACT_SNAPSHOT_PATH, compact_snapshot(snapshot))
    write_json(STATS_PATH, statistics)
    print(
        f"Saved {len(snapshot['routes'])} routes, {len(statistics)} statistic rows, "
        f"and {len(snapshot['failures'])} failures.",
        flush=True,
    )


if __name__ == "__main__":
    main()
