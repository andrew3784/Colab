from __future__ import annotations

import argparse

from sqlalchemy import text

from flood_analysis.db import get_engine, init_db
from flood_analysis.noaa import NoaaRequest, fetch_station_metadata, fetch_water_levels


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest NOAA CO-OPS water levels into PostGIS.")
    parser.add_argument("--station", required=True, help="NOAA station ID, e.g. 8638610 for Sewells Point.")
    parser.add_argument("--begin-date", required=True, help="YYYYMMDD")
    parser.add_argument("--end-date", required=True, help="YYYYMMDD")
    parser.add_argument("--datum", default="MLLW")
    parser.add_argument("--units", default="english", choices=["english", "metric"])
    parser.add_argument("--interval", default="6", help="NOAA interval, commonly 6 or h.")
    return parser.parse_args()


def upsert_station(conn, station_id: str, metadata: dict) -> None:
    lat = float(metadata.get("lat")) if metadata.get("lat") is not None else None
    lng = float(metadata.get("lng")) if metadata.get("lng") is not None else None
    conn.execute(
        text(
            """
            INSERT INTO raw.noaa_stations (station_id, name, latitude, longitude, state, timezone, geom)
            VALUES (:station_id, :name, :latitude, :longitude, :state, :timezone,
                    CASE WHEN :longitude IS NULL OR :latitude IS NULL THEN NULL
                         ELSE ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)
                    END)
            ON CONFLICT (station_id) DO UPDATE SET
                name = EXCLUDED.name,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                state = EXCLUDED.state,
                timezone = EXCLUDED.timezone,
                updated_at = now(),
                geom = EXCLUDED.geom
            """
        ),
        {
            "station_id": station_id,
            "name": metadata.get("name"),
            "latitude": lat,
            "longitude": lng,
            "state": metadata.get("state"),
            "timezone": metadata.get("timezone"),
        },
    )


def upsert_water_levels(conn, rows: list[dict]) -> None:
    if not rows:
        return
    conn.execute(
        text(
            """
            INSERT INTO raw.noaa_water_levels
                (station_id, observed_at, water_level, sigma, flags, quality, datum, units, product)
            VALUES
                (:station_id, :observed_at, :water_level, :sigma, :flags, :quality, :datum, :units, :product)
            ON CONFLICT (station_id, observed_at, datum, units, product) DO UPDATE SET
                water_level = EXCLUDED.water_level,
                sigma = EXCLUDED.sigma,
                flags = EXCLUDED.flags,
                quality = EXCLUDED.quality,
                ingested_at = now()
            """
        ),
        rows,
    )


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)

    request = NoaaRequest(
        station=args.station,
        begin_date=args.begin_date,
        end_date=args.end_date,
        datum=args.datum,
        units=args.units,
        interval=args.interval,
    )
    metadata = fetch_station_metadata(args.station)
    water_levels = fetch_water_levels(request)

    with engine.begin() as conn:
        upsert_station(conn, args.station, metadata)
        upsert_water_levels(conn, water_levels.to_dict(orient="records"))

    print(f"Ingested {len(water_levels)} NOAA water-level observations for station {args.station}.")


if __name__ == "__main__":
    main()
