from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from flood_analysis.noaa import fetch_station_datums, fetch_station_metadata


def upsert_station_datums(engine: Engine, station_id: str) -> int:
    metadata = fetch_station_metadata(station_id)
    datums = fetch_station_datums(station_id)
    rows = [
        {
            "station_id": station_id,
            "datum_name": datum["name"],
            "description": datum.get("description"),
            "value": datum["value"],
            "units": datums["units"],
            "orthometric_datum": datums.get("OrthometricDatum"),
            "epoch": datums.get("epoch"),
            "accepted": datums.get("accepted"),
            "superseded": datums.get("superseded"),
        }
        for datum in datums["datums"]
    ]
    station_query = text(
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
    )
    datums_query = text(
        """
        INSERT INTO raw.noaa_station_datums (
            station_id, datum_name, description, value, units, orthometric_datum, epoch, accepted, superseded
        )
        VALUES (
            :station_id, :datum_name, :description, :value, :units, :orthometric_datum, :epoch, :accepted, :superseded
        )
        ON CONFLICT (station_id, datum_name) DO UPDATE SET
            description = EXCLUDED.description,
            value = EXCLUDED.value,
            units = EXCLUDED.units,
            orthometric_datum = EXCLUDED.orthometric_datum,
            epoch = EXCLUDED.epoch,
            accepted = EXCLUDED.accepted,
            superseded = EXCLUDED.superseded,
            updated_at = now()
        """
    )
    latitude = float(metadata.get("lat")) if metadata.get("lat") is not None else None
    longitude = float(metadata.get("lng")) if metadata.get("lng") is not None else None
    with engine.begin() as conn:
        conn.execute(
            station_query,
            {
                "station_id": station_id,
                "name": metadata.get("name"),
                "latitude": latitude,
                "longitude": longitude,
                "state": metadata.get("state"),
                "timezone": metadata.get("timezone"),
            },
        )
        conn.execute(datums_query, rows)
    return len(rows)


def convert_scenarios_to_datum(engine: Engine, station_id: str, source_datum: str, target_datum: str) -> int:
    query = text(
        """
        WITH datum_offsets AS (
            SELECT
                src.value AS source_value,
                tgt.value AS target_value,
                src.units AS source_units,
                tgt.units AS target_units
            FROM raw.noaa_station_datums src
            JOIN raw.noaa_station_datums tgt
              ON tgt.station_id = src.station_id
            WHERE src.station_id = :station_id
              AND src.datum_name = :source_datum
              AND tgt.datum_name = :target_datum
        )
        UPDATE processed.flood_scenarios scenarios
        SET
            analysis_datum = :target_datum,
            analysis_water_surface_elevation = scenarios.water_surface_elevation
                + datum_offsets.source_value
                - datum_offsets.target_value,
            method = CASE
                WHEN scenarios.method ILIKE '%' || 'converted from ' || :source_datum || ' to ' || :target_datum || '%' THEN scenarios.method
                ELSE CONCAT_WS('; ', NULLIF(scenarios.method, ''),
                    'converted from ' || :source_datum || ' to ' || :target_datum || ' using NOAA station datum offsets')
            END
        FROM datum_offsets
        WHERE scenarios.source_station_id = :station_id
          AND scenarios.datum = :source_datum
          AND scenarios.units = 'english'
          AND datum_offsets.source_units = 'feet'
          AND datum_offsets.target_units = 'feet'
        """
    )
    with engine.begin() as conn:
        result = conn.execute(
            query,
            {
                "station_id": station_id,
                "source_datum": source_datum,
                "target_datum": target_datum,
            },
        )
    return result.rowcount
