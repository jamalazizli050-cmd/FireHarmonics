"""Static metadata: FIRMS data sources and geographic regions.

Source identifiers are exactly those documented at
https://firms.modaps.eosdis.nasa.gov/api/area/ (verified 2026-11-13).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np


@dataclass(frozen=True)
class Platform:
    name: str
    # First date fire data are available for this platform. Used for coverage
    # bookkeeping (e.g. MODIS_SP before Aqua's first data is Terra-only).
    start: date
    # Code(s) used in the FIRMS ``satellite`` column.
    codes: tuple[str, ...]


TERRA = Platform("Terra", date(2000, 11, 1), ("Terra", "T"))
AQUA = Platform("Aqua", date(2002, 7, 4), ("Aqua", "A"))
SNPP = Platform("Suomi NPP", date(2012, 1, 20), ("N",))
NOAA20 = Platform("NOAA-20", date(2018, 4, 1), ("1", "N20"))
NOAA21 = Platform("NOAA-21", date(2023, 3, 1), ("N21", "2"))


@dataclass(frozen=True)
class Source:
    id: str
    sensor: str              # "MODIS" or "VIIRS"
    processing: str          # "SP" (standard) or "NRT"
    platforms: tuple[Platform, ...]
    nominal_resolution_m: int
    brightness_col: str      # primary brightness temperature column in the raw CSV
    brightness_aux_col: str  # secondary brightness temperature column

    @property
    def is_standard(self) -> bool:
        return self.processing == "SP"


_MODIS = dict(sensor="MODIS", platforms=(TERRA, AQUA), nominal_resolution_m=1000,
              brightness_col="brightness", brightness_aux_col="bright_t31")
_VIIRS = dict(sensor="VIIRS", nominal_resolution_m=375,
              brightness_col="bright_ti4", brightness_aux_col="bright_ti5")

SOURCES: dict[str, Source] = {s.id: s for s in [
    Source("MODIS_SP", processing="SP", **_MODIS),
    Source("MODIS_NRT", processing="NRT", **_MODIS),
    Source("VIIRS_SNPP_SP", processing="SP", platforms=(SNPP,), **_VIIRS),
    Source("VIIRS_SNPP_NRT", processing="NRT", platforms=(SNPP,), **_VIIRS),
    Source("VIIRS_NOAA20_SP", processing="SP", platforms=(NOAA20,), **_VIIRS),
    Source("VIIRS_NOAA20_NRT", processing="NRT", platforms=(NOAA20,), **_VIIRS),
    Source("VIIRS_NOAA21_SP", processing="SP", platforms=(NOAA21,), **_VIIRS),
    Source("VIIRS_NOAA21_NRT", processing="NRT", platforms=(NOAA21,), **_VIIRS),
]}

# Columns returned by the Area API (verified against live responses).
RAW_COLUMNS = {
    "MODIS": ["latitude", "longitude", "brightness", "scan", "track", "acq_date", "acq_time",
              "satellite", "instrument", "confidence", "version", "bright_t31", "frp",
              "daynight", "type"],
    "VIIRS": ["latitude", "longitude", "bright_ti4", "scan", "track", "acq_date", "acq_time",
              "satellite", "instrument", "confidence", "version", "bright_ti5", "frp",
              "daynight", "type"],
}
# NRT products do not include ``type``; it is optional.
OPTIONAL_RAW_COLUMNS = {"type"}


def get_source(source_id: str) -> Source:
    try:
        return SOURCES[source_id]
    except KeyError:
        raise ValueError(f"Unknown FIRMS source {source_id!r}; expected one of {sorted(SOURCES)}")


def platform_for_code(source: Source, code: str) -> str | None:
    for p in source.platforms:
        if code in p.codes:
            return p.name
    return None


@dataclass(frozen=True)
class BBox:
    """Geographic bounding box in decimal degrees (WGS84)."""
    west: float
    south: float
    east: float
    north: float

    def __post_init__(self):
        if not (-180 <= self.west < self.east <= 180):
            raise ValueError(f"Invalid longitudes: west={self.west}, east={self.east}")
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError(f"Invalid latitudes: south={self.south}, north={self.north}")

    @property
    def is_global(self) -> bool:
        return (self.west, self.south, self.east, self.north) == (-180, -90, 180, 90)

    def to_api(self) -> str:
        if self.is_global:
            return "world"
        return ",".join(f"{v:g}" for v in (self.west, self.south, self.east, self.north))

    def key(self) -> str:
        return "world" if self.is_global else "bbox_" + "_".join(
            f"{v:g}" for v in (self.west, self.south, self.east, self.north))

    def contains(self, lat, lon):
        lat = np.asarray(lat, dtype=float)
        lon = np.asarray(lon, dtype=float)
        return (lat >= self.south) & (lat <= self.north) & (lon >= self.west) & (lon <= self.east)

    def split(self, n_lon: int = 1, n_lat: int = 1) -> list["BBox"]:
        """Split into tiles, e.g. to keep global requests below size limits."""
        lons = np.linspace(self.west, self.east, n_lon + 1)
        lats = np.linspace(self.south, self.north, n_lat + 1)
        return [BBox(float(lons[i]), float(lats[j]), float(lons[i + 1]), float(lats[j + 1]))
                for i in range(n_lon) for j in range(n_lat)]

    @classmethod
    def parse(cls, text: str) -> "BBox":
        if text.strip().lower() == "world":
            return WORLD
        parts = [float(p) for p in text.split(",")]
        if len(parts) != 4:
            raise ValueError("Bounding box must be 'west,south,east,north' or 'world'")
        return cls(*parts)


WORLD = BBox(-180, -90, 180, 90)


@dataclass(frozen=True)
class Region:
    name: str
    bbox: BBox
    description: str = ""


REGIONS: dict[str, Region] = {r.name: r for r in [
    Region("world", WORLD, "Global coverage"),
    # Rectangle enclosing the State of California. It necessarily also
    # includes parts of Oregon, Nevada, Arizona, Baja California and ocean.
    Region("california", BBox(-124.5, 32.5, -114.0, 42.1),
           "Bounding box enclosing California (not the state boundary)"),
    # Contrasting fire regimes used to test transferability of harmonisation.
    Region("zambia_angola", BBox(22.0, -15.0, 26.0, -11.0),
           "Miombo woodland / savanna, frequent dry-season (May-Oct) burning"),
    Region("rondonia", BBox(-64.0, -12.0, -60.0, -8.0),
           "Southern Amazon deforestation and pasture fires (Jul-Oct)"),
    Region("yakutia", BBox(120.0, 60.0, 130.0, 65.0),
           "Boreal forest (Sakha Republic), large summer fires; overlapping polar-orbit swaths"),
    Region("punjab", BBox(73.5, 29.5, 77.0, 32.5),
           "Agricultural residue burning (Apr-May, Oct-Nov): many small, short-lived fires"),
]}

HARMONIZATION_REGIONS = ["california", "zambia_angola", "rondonia", "yakutia", "punjab"]


def resolve_region(name_or_bbox: str) -> Region:
    if name_or_bbox in REGIONS:
        return REGIONS[name_or_bbox]
    bbox = BBox.parse(name_or_bbox)
    return Region(bbox.key(), bbox, "Custom bounding box")
