"""Build a compact offline Greater London place and street-name search index.

The source is OS Open Names, copyright Ordnance Survey, licensed under the
Open Government Licence v3.0. Download the current CSV archive from OS Data
Hub, then run this script with the archive path as its only argument.
"""

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile


# Scripts live one level below the application and its bundled data directory.
PROJECT_DIRECTORY = Path(__file__).parent.parent
OUTPUT_PATH = PROJECT_DIRECTORY / "data" / "london_location_index.json"
LONDON_COUNTY = "Greater London"
OS_OPEN_NAMES_COLUMNS = (
    "ID", "NAMES_URI", "NAME1", "NAME1_LANG", "NAME2", "NAME2_LANG", "TYPE", "LOCAL_TYPE",
    "GEOMETRY_X", "GEOMETRY_Y", "MOST_DETAIL_VIEW_RES", "LEAST_DETAIL_VIEW_RES", "MBR_XMIN", "MBR_YMIN",
    "MBR_XMAX", "MBR_YMAX", "POSTCODE_DISTRICT", "POSTCODE_DISTRICT_URI", "POPULATED_PLACE",
    "POPULATED_PLACE_URI", "POPULATED_PLACE_TYPE", "DISTRICT_BOROUGH", "DISTRICT_BOROUGH_URI",
    "DISTRICT_BOROUGH_TYPE", "COUNTY_UNITARY", "COUNTY_UNITARY_URI", "COUNTY_UNITARY_TYPE", "REGION",
    "REGION_URI", "COUNTRY", "COUNTRY_URI", "RELATED_SPATIAL_OBJECT", "SAME_AS_DBPEDIA", "SAME_AS_GEONAMES",
)
AIRY_1830 = 6_377_563.396
AIRY_1830_MINOR_AXIS = 6_356_256.909
NATIONAL_GRID_SCALE = 0.9996012717
NATIONAL_GRID_LATITUDE_ORIGIN = math.radians(49)
NATIONAL_GRID_LONGITUDE_ORIGIN = math.radians(-2)
NATIONAL_GRID_FALSE_NORTHING = -100_000
NATIONAL_GRID_FALSE_EASTING = 400_000


def osgb36_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """Convert British National Grid coordinates to WGS84 latitude and longitude."""
    eccentricity_squared = 1 - (AIRY_1830_MINOR_AXIS**2 / AIRY_1830**2)
    latitude = NATIONAL_GRID_LATITUDE_ORIGIN
    meridional_arc = 0.0
    while northing - NATIONAL_GRID_FALSE_NORTHING - meridional_arc >= 0.00001:
        latitude += (northing - NATIONAL_GRID_FALSE_NORTHING - meridional_arc) / (
            AIRY_1830 * NATIONAL_GRID_SCALE
        )
        latitude_delta = latitude - NATIONAL_GRID_LATITUDE_ORIGIN
        meridional_arc = NATIONAL_GRID_SCALE * (
            (1 + eccentricity_squared * (1 / 4 + eccentricity_squared * (3 / 64 + 5 * eccentricity_squared / 256)))
            * AIRY_1830
            * latitude_delta
            - (3 * eccentricity_squared / 8 + eccentricity_squared**2 * (3 / 32 + 45 * eccentricity_squared / 1024))
            * AIRY_1830
            * math.sin(latitude_delta)
            * math.cos(latitude + NATIONAL_GRID_LATITUDE_ORIGIN)
            + (15 * eccentricity_squared**2 / 256 + 45 * eccentricity_squared**3 / 1024)
            * AIRY_1830
            * math.sin(2 * latitude_delta)
            * math.cos(2 * (latitude + NATIONAL_GRID_LATITUDE_ORIGIN))
            - 35 * eccentricity_squared**3 / 3072 * AIRY_1830 * math.sin(3 * latitude_delta) * math.cos(3 * (latitude + NATIONAL_GRID_LATITUDE_ORIGIN))
        )
    transverse_radius = AIRY_1830 * NATIONAL_GRID_SCALE / math.sqrt(1 - eccentricity_squared * math.sin(latitude) ** 2)
    meridional_radius = AIRY_1830 * NATIONAL_GRID_SCALE * (1 - eccentricity_squared) / (1 - eccentricity_squared * math.sin(latitude) ** 2) ** 1.5
    eta_squared = transverse_radius / meridional_radius - 1
    tangent = math.tan(latitude)
    secant = 1 / math.cos(latitude)
    delta_easting = easting - NATIONAL_GRID_FALSE_EASTING
    latitude -= tangent / (2 * meridional_radius * transverse_radius) * delta_easting**2
    latitude += tangent / (24 * meridional_radius * transverse_radius**3) * (5 + 3 * tangent**2 + eta_squared - 9 * tangent**2 * eta_squared) * delta_easting**4
    latitude -= tangent / (720 * meridional_radius * transverse_radius**5) * (61 + 90 * tangent**2 + 45 * tangent**4) * delta_easting**6
    longitude = NATIONAL_GRID_LONGITUDE_ORIGIN + secant / transverse_radius * delta_easting
    longitude -= secant / (6 * transverse_radius**3) * (transverse_radius / meridional_radius + 2 * tangent**2) * delta_easting**3
    longitude += secant / (120 * transverse_radius**5) * (5 + 28 * tangent**2 + 24 * tangent**4) * delta_easting**5

    # Helmert transform from OSGB36 to WGS84, followed by a WGS84 ellipsoid conversion.
    height = 0.0
    radius = AIRY_1830 / math.sqrt(1 - eccentricity_squared * math.sin(latitude) ** 2)
    x1 = (radius + height) * math.cos(latitude) * math.cos(longitude)
    y1 = (radius + height) * math.cos(latitude) * math.sin(longitude)
    z1 = ((1 - eccentricity_squared) * radius + height) * math.sin(latitude)
    scale = 20.4894e-6
    rotation_x, rotation_y, rotation_z = [math.radians(value / 3600) for value in (0.1502, 0.2470, 0.8421)]
    x2 = 446.448 + (1 + scale) * x1 - rotation_z * y1 + rotation_y * z1
    y2 = -125.157 + rotation_z * x1 + (1 + scale) * y1 - rotation_x * z1
    z2 = 542.060 - rotation_y * x1 + rotation_x * y1 + (1 + scale) * z1
    wgs84_major_axis = 6_378_137.0
    wgs84_eccentricity_squared = 0.00669438037928458
    longitude_wgs84 = math.atan2(y2, x2)
    latitude_wgs84 = math.atan2(z2, math.sqrt(x2**2 + y2**2) * (1 - wgs84_eccentricity_squared))
    for _ in range(8):
        radius = wgs84_major_axis / math.sqrt(1 - wgs84_eccentricity_squared * math.sin(latitude_wgs84) ** 2)
        latitude_wgs84 = math.atan2(z2 + wgs84_eccentricity_squared * radius * math.sin(latitude_wgs84), math.sqrt(x2**2 + y2**2))
    return math.degrees(latitude_wgs84), math.degrees(longitude_wgs84)


def build_index(archive_path: Path) -> list[dict[str, object]]:
    """Read London records and merge road sections into one useful target per area."""
    grouped_locations: dict[tuple[str, str, str, str], list[tuple[float, float]]] = defaultdict(list)
    with ZipFile(archive_path) as archive:
        for filename in archive.namelist():
            if not filename.startswith("Data/") or not filename.endswith(".csv"):
                continue
            with archive.open(filename, "r") as csv_file:
                reader = csv.DictReader(
                    (line.decode("utf-8-sig") for line in csv_file),
                    fieldnames=OS_OPEN_NAMES_COLUMNS,
                )
                for row in reader:
                    if row["COUNTY_UNITARY"] != LONDON_COUNTY or not row["NAME1"]:
                        continue
                    kind = "road" if "Road" in row["LOCAL_TYPE"] else "area"
                    if kind == "area" and row["TYPE"] != "populatedPlace":
                        continue
                    locality = row["POPULATED_PLACE"] or ""
                    borough = row["DISTRICT_BOROUGH"] or ""
                    key = (row["NAME1"], kind, locality, borough)
                    grouped_locations[key].append((float(row["GEOMETRY_X"]), float(row["GEOMETRY_Y"])))

    locations = []
    for (name, kind, locality, borough), coordinates in grouped_locations.items():
        easting = sum(point[0] for point in coordinates) / len(coordinates)
        northing = sum(point[1] for point in coordinates) / len(coordinates)
        latitude, longitude = osgb36_to_wgs84(easting, northing)
        locations.append(
            {
                "name": name,
                "kind": kind,
                "locality": locality,
                "borough": borough,
                "lat": round(latitude, 6),
                "lon": round(longitude, 6),
            }
        )
    return sorted(locations, key=lambda location: (str(location["name"]).casefold(), str(location["borough"]).casefold()))


def main() -> None:
    """Build the version-controlled offline index from an OS Open Names CSV ZIP archive."""
    if len(sys.argv) != 2:
        raise SystemExit("Usage: uv run build_location_index.py /path/to/opname_csv_gb.zip")
    locations = build_index(Path(sys.argv[1]))
    with OUTPUT_PATH.open("w") as output_file:
        json.dump(locations, output_file, separators=(",", ":"))
    print(f"Wrote {len(locations)} London places and roads to {OUTPUT_PATH}.")


if __name__ == "__main__":
    main()
