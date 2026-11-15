"""Descriptive statistics with explicit coverage; no predictive model."""
import calendar
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev

from fastapi import HTTPException

EARTH_RADIUS_KM = 6371.0088


def bounding_boxes(latitude, longitude, radius_km):
    angle = radius_km / EARTH_RADIUS_KM
    delta_lat = math.degrees(angle)
    south, north = max(-90, latitude - delta_lat), min(90, latitude + delta_lat)
    if south == -90 or north == 90:
        return [(-180.0, south, 180.0, north)]
    delta_lon = math.degrees(math.asin(min(1.0, math.sin(angle) / math.cos(math.radians(latitude)))))
    west, east = longitude - delta_lon, longitude + delta_lon
    if west < -180:
        return [(-180.0, south, east, north), (west + 360, south, 180.0, north)]
    if east > 180:
        return [(west, south, 180.0, north), (-180.0, south, east - 360, north)]
    return [(west, south, east, north)]


def distance_km(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    delta_lat, delta_lon = b - a, math.radians(lon2 - lon1)
    value = math.sin(delta_lat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, max(0.0, value))))


def finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def normalize_rows(rows):
    normalized = []
    seen = set()
    for index, row in enumerate(rows, start=1):
        try:
            latitude, longitude = float(row["latitude"]), float(row["longitude"])
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError
            day = date.fromisoformat(str(row["acq_date"]))
            raw_time = str(row.get("acq_time", "")).strip()
            acquired = None
            if raw_time:
                value = raw_time.zfill(4)
                acquired = datetime.combine(day, datetime.strptime(value, "%H%M").time(), tzinfo=timezone.utc).isoformat()
            identity = (latitude, longitude, str(day), raw_time.zfill(4) if raw_time else "", str(row.get("satellite", "")), str(row.get("instrument", "")))
            if identity in seen:
                continue
            seen.add(identity)
            normalized.append({
                "latitude": latitude, "longitude": longitude,
                "acq_date": str(day), "acquired_at": acquired,
                "frp": finite_number(row.get("frp")),
                "confidence": row.get("confidence"),
                "satellite": row.get("satellite"), "instrument": row.get("instrument"),
                "raw": row,
            })
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(422, f"Detection row {index} must have valid latitude, longitude, acq_date and optional HHMM acq_time.") from exc
    return normalized


def filter_rows(rows, request, start, end):
    return [row for row in rows if str(start) <= row["acq_date"] <= str(end)
            and distance_km(request.latitude, request.longitude, row["latitude"], row["longitude"]) <= request.radius_km + 1e-8]


def describe(rows, request, coverage, warnings=None):
    start, end = date.fromisoformat(coverage["start_date"]), date.fromisoformat(coverage["end_date"])
    complete = coverage["complete"]
    dates = Counter(row["acq_date"] for row in rows)
    monthly = Counter(row["acq_date"][:7] for row in rows)
    yearly = Counter(int(row["acq_date"][:4]) for row in rows)
    complete_years = [year for year in range(start.year, end.year + 1)
                      if complete and start <= date(year, 1, 1) and end >= date(year, 12, 31)]
    yearly_counts = [{"year": year, "detections": yearly[year], "complete": year in complete_years}
                     for year in range(start.year, end.year + 1)]
    daily_counts = []
    cursor = start
    while cursor <= end:
        if complete or str(cursor) in dates:
            daily_counts.append({"date": str(cursor), "detections": dates[str(cursor)]})
        cursor += timedelta(days=1)
    monthly_counts = []
    cursor = start.replace(day=1)
    while cursor <= end:
        month_end = date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1])
        monthly_counts.append({"month": cursor.strftime("%Y-%m"), "detections": monthly[cursor.strftime("%Y-%m")],
                               "complete": complete and start <= cursor and end >= month_end})
        cursor = (month_end + timedelta(days=1))
    seasonal = {"status": "insufficient_data", "peak_month": None, "monthly_mean_detections": [],
                "explanation": "At least two complete calendar years are required."}
    if len(complete_years) >= 2:
        counts = Counter(int(row["acq_date"][5:7]) for row in rows if int(row["acq_date"][:4]) in complete_years)
        seasonal = {"status": "available", "peak_month": max(range(1, 13), key=lambda m: counts[m]) if sum(counts.values()) else None,
                    "monthly_mean_detections": [{"month": m, "detections": counts[m] / len(complete_years)} for m in range(1, 13)],
                    "complete_years": complete_years,
                    "explanation": "Mean observed detections by month across complete calendar years; not a forecast."}
    trends = {"status": "insufficient_data", "slope_detections_per_year": None,
              "direction": None, "explanation": "At least three complete calendar years are required."}
    if len(complete_years) >= 3:
        x_mean, y_mean = mean(complete_years), mean(yearly[y] for y in complete_years)
        slope = sum((y - x_mean) * (yearly[y] - y_mean) for y in complete_years) / sum((y - x_mean) ** 2 for y in complete_years)
        trends = {"status": "available", "slope_detections_per_year": slope,
                  "direction": "increasing" if slope > 1e-9 else "decreasing" if slope < -1e-9 else "flat",
                  "complete_years": complete_years, "method": "ordinary least squares on annual detection counts",
                  "explanation": "Descriptive direction only; no statistical significance or future risk is implied."}
    full_months = [item for item in monthly_counts if item["complete"]]
    anomalies = {"status": "insufficient_data", "items": [], "explanation": "At least 12 complete months are required."}
    if len(full_months) >= 12:
        baseline = mean(item["detections"] for item in full_months)
        deviation = pstdev(item["detections"] for item in full_months)
        items = []
        if deviation:
            for item in full_months:
                z_score = (item["detections"] - baseline) / deviation
                if abs(z_score) >= 2:
                    items.append({**item, "z_score": z_score})
        anomalies = {"status": "available", "items": items, "method": "absolute monthly count z-score >= 2",
                     "explanation": "Descriptive outliers against the period mean; not adjusted for seasonality or observation coverage."}
    cells = defaultdict(list)
    for row in rows:
        cells[(math.floor(row["latitude"] * 100), math.floor(row["longitude"] * 100))].append(row)
    hotspots = []
    for group in cells.values():
        power = [row["frp"] for row in group if row["frp"] is not None]
        hotspots.append({"latitude": mean(row["latitude"] for row in group), "longitude": mean(row["longitude"] for row in group),
                         "detections": len(group), "max_frp_mw": max(power) if power else None,
                         "first_date": min(row["acq_date"] for row in group), "last_date": max(row["acq_date"] for row in group),
                         "cell_size_degrees": 0.01})
    hotspots.sort(key=lambda item: (-item["detections"], item["latitude"], item["longitude"]))
    notes = list(warnings or [])
    notes.append("Detections are satellite observations, not a count of distinct fires or a probability of fire.")
    notes.append("Satellite coverage, clouds, sensor changes and processing quality affect detection counts.")
    if not complete:
        notes.append("The archive was not declared complete. Missing dates must not be interpreted as zero detections.")
    if len(complete_years) < 2:
        notes.append("The coverage is too short or incomplete to estimate seasonality.")
    if len(hotspots) > 100:
        notes.append("Only the 100 grid cells with the most detections are included in hotspots.")
    return {
        "location": {"latitude": request.latitude, "longitude": request.longitude, "radius_km": request.radius_km},
        "source": request.source.value, "coverage": coverage, "detection_count": len(rows),
        "historical_activity": {
            "mean_annual_detections": mean(yearly[year] for year in complete_years) if complete_years else None,
            "complete_years": complete_years, "daily_counts": daily_counts,
            "monthly_counts": monthly_counts, "annual_counts": yearly_counts,
            "explanation": "Annual mean uses complete calendar years only. Empty complete intervals count as zero.",
        },
        "seasonal_patterns": seasonal, "long_term_trends": trends, "anomalies": anomalies,
        "hotspots": hotspots[:100],
        "prediction": {"probability": None, "explanation": "A prediction model has not been added. Observed detections are not a forecast."},
        "warnings": notes,
    }
