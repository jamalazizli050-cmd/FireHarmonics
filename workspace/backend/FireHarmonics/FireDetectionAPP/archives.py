"""Read NASA exports in memory. Never extract zip paths or execute archive content."""
import io
import json
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

import shapefile
from fastapi import HTTPException

from .nasa import parse_csv

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_ROWS = 200_000


def json_rows(content):
    def reject_nonfinite(value):
        raise ValueError("Non-finite JSON number")

    value = json.loads(content.decode("utf-8-sig"), parse_constant=reject_nonfinite)
    if isinstance(value, dict) and value.get("type") == "FeatureCollection":
        rows = []
        for feature in value.get("features", []):
            geometry = feature.get("geometry") or {}
            if geometry.get("type") != "Point":
                raise HTTPException(422, "Archive GeoJSON must contain Point features")
            lon, lat, *_ = geometry["coordinates"]
            rows.append({**feature.get("properties", {}), "latitude": lat, "longitude": lon})
        return rows
    if isinstance(value, dict):
        value = value.get("rows", value.get("data"))
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise HTTPException(422, "JSON must be a list of detections, an object containing rows/data, or a GeoJSON FeatureCollection")
    return value


def parse_archive(content, filename):
    try:
        suffix = PurePosixPath(filename or "").suffix.lower()
        if suffix == ".csv":
            rows = parse_csv(content.decode("utf-8-sig"))
        elif suffix in {".json", ".geojson"}:
            rows = json_rows(content)
        elif suffix == ".zip":
            with ZipFile(io.BytesIO(content)) as archive:
                members = [item for item in archive.infolist() if not item.is_dir() and not item.filename.startswith("__MACOSX/")]
                if len(members) > 100:
                    raise HTTPException(413, "ZIP contains more than 100 files")
                if sum(item.file_size for item in members) > MAX_EXPANDED_BYTES:
                    raise HTTPException(413, "Expanded archive exceeds 100 MiB")
                names = [item.filename for item in members]
                shape_files = [name for name in names if name.lower().endswith(".shp")]
                if shape_files:
                    if len(shape_files) != 1:
                        raise HTTPException(422, "Upload one shapefile dataset per ZIP")
                    shp_name = shape_files[0]
                    stem = str(PurePosixPath(shp_name).with_suffix(""))
                    dbf_name = next((name for name in names if name.lower() == (stem + ".dbf").lower()), None)
                    if not dbf_name:
                        raise HTTPException(422, "Shapefile ZIP must include matching .shp and .dbf files")
                    prj_name = next((name for name in names if name.lower() == (stem + ".prj").lower()), None)
                    if prj_name and "PROJCS" in archive.read(prj_name).decode("utf-8-sig").upper():
                        raise HTTPException(422, "Reproject the shapefile to WGS84 longitude/latitude before uploading")
                    reader = shapefile.Reader(shp=io.BytesIO(archive.read(shp_name)), dbf=io.BytesIO(archive.read(dbf_name)), encoding="utf-8")
                    with reader:
                        if len(reader) > MAX_ROWS:
                            raise HTTPException(413, "Archive exceeds 200000 rows")
                        if reader.shapeType not in {shapefile.POINT, shapefile.POINTZ, shapefile.POINTM}:
                            raise HTTPException(422, "Only point shapefiles in longitude/latitude coordinates are supported")
                        rows = []
                        for item in reader.iterShapeRecords():
                            row = item.record.as_dict()
                            lon, lat = item.shape.points[0][:2]
                            rows.append({**row, "latitude": lat, "longitude": lon})
                else:
                    supported = [name for name in names if PurePosixPath(name).suffix.lower() in {".csv", ".json", ".geojson"}]
                    if not supported:
                        raise HTTPException(415, "ZIP must contain CSV, JSON, GeoJSON, or a point shapefile")
                    rows = []
                    for name in supported:
                        rows.extend(parse_archive(archive.read(name), name))
                        if len(rows) > MAX_ROWS:
                            raise HTTPException(413, "Archive exceeds 200000 rows")
        else:
            raise HTTPException(415, "Upload .csv, .json, .geojson or .zip")
        if len(rows) > MAX_ROWS:
            raise HTTPException(413, "Archive exceeds 200000 rows")
        # DBF field names are commonly uppercase and dates may be native objects.
        return [{str(key).lower(): str(value) if hasattr(value, "isoformat") else value for key, value in row.items()} for row in rows]
    except HTTPException as exc:
        if exc.status_code == 502:
            raise HTTPException(422, "The uploaded archive contains invalid CSV") from exc
        raise
    except (UnicodeError, ValueError, TypeError, KeyError, IndexError, BadZipFile, RuntimeError, shapefile.ShapefileException) as exc:
        raise HTTPException(422, "Could not parse the uploaded archive") from exc
