from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests


API_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
STATION_URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{station}.json"
DATUMS_URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{station}/datums.json"


@dataclass(frozen=True)
class NoaaRequest:
    station: str
    begin_date: str
    end_date: str
    datum: str = "MLLW"
    units: str = "english"
    interval: str = "6"
    time_zone: str = "gmt"
    product: str = "water_level"


def fetch_station_metadata(station: str) -> dict[str, Any]:
    response = requests.get(STATION_URL.format(station=station), params={"expand": "details"}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    stations = payload.get("stations") or []
    if not stations:
        raise RuntimeError(f"NOAA station metadata not found for station {station}")
    return stations[0]


def fetch_station_datums(station: str) -> dict[str, Any]:
    response = requests.get(DATUMS_URL.format(station=station), timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("datums"):
        raise RuntimeError(f"NOAA station datums not found for station {station}")
    return payload


def fetch_water_levels(request: NoaaRequest) -> pd.DataFrame:
    params = {
        "product": request.product,
        "application": "hampton_roads_flood_analysis",
        "begin_date": request.begin_date,
        "end_date": request.end_date,
        "datum": request.datum,
        "station": request.station,
        "time_zone": request.time_zone,
        "units": request.units,
        "interval": request.interval,
        "format": "json",
    }
    response = requests.get(API_URL, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(payload["error"].get("message", payload["error"]))
    data = payload.get("data") or []
    if not data:
        return pd.DataFrame(columns=["station_id", "observed_at", "water_level", "sigma", "flags", "quality", "datum", "units", "product"])

    df = pd.DataFrame(data)
    df = df.rename(columns={"t": "observed_at", "v": "water_level", "s": "sigma", "f": "flags", "q": "quality"})
    df["station_id"] = request.station
    df["observed_at"] = pd.to_datetime(df["observed_at"], utc=True)
    df["water_level"] = pd.to_numeric(df["water_level"], errors="coerce")
    df["sigma"] = pd.to_numeric(df.get("sigma"), errors="coerce")
    df["datum"] = request.datum
    df["units"] = request.units
    df["product"] = request.product
    columns = ["station_id", "observed_at", "water_level", "sigma", "flags", "quality", "datum", "units", "product"]
    return df.reindex(columns=columns)
