from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class PeakEvent:
    station_id: str
    event_start: datetime
    event_end: datetime
    peak_observed_at: datetime
    peak_water_level: float
    datum: str
    units: str


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "scenario"


def get_peak_event(
    engine: Engine,
    station_id: str,
    event_start: str,
    event_end: str,
    datum: str,
    units: str,
) -> PeakEvent:
    query = text(
        """
        SELECT
            station_id,
            CAST(:event_start AS timestamptz) AS event_start,
            CAST(:event_end AS timestamptz) AS event_end,
            observed_at AS peak_observed_at,
            water_level AS peak_water_level,
            datum,
            units
        FROM raw.noaa_water_levels
        WHERE station_id = :station_id
          AND observed_at >= CAST(:event_start AS timestamptz)
          AND observed_at <= CAST(:event_end AS timestamptz)
          AND datum = :datum
          AND units = :units
          AND water_level IS NOT NULL
        ORDER BY water_level DESC, observed_at ASC
        LIMIT 1
        """
    )
    with engine.connect() as conn:
        row = conn.execute(
            query,
            {
                "station_id": station_id,
                "event_start": event_start,
                "event_end": event_end,
                "datum": datum,
                "units": units,
            },
        ).mappings().first()
    if row is None:
        raise RuntimeError(
            "No NOAA water levels found for the requested station/date/datum/units. "
            "Run scripts/ingest_noaa_water_levels.py first."
        )
    return PeakEvent(**row)


def upsert_peak_scenarios(
    engine: Engine,
    peak: PeakEvent,
    study_area: str,
    sea_level_rise_values: list[float],
    method: str,
    notes: str | None = None,
) -> list[str]:
    scenario_ids: list[str] = []
    study_area_slug = slugify(study_area)
    event_slug = peak.event_start.strftime("%Y%m%d")
    query = text(
        """
        INSERT INTO processed.flood_scenarios (
            scenario_id,
            name,
            source_station_id,
            event_start,
            event_end,
            peak_observed_at,
            peak_water_level,
            water_surface_elevation,
            analysis_datum,
            analysis_water_surface_elevation,
            datum,
            units,
            sea_level_rise_ft,
            study_area,
            method,
            notes
        )
        VALUES (
            :scenario_id,
            :name,
            :source_station_id,
            :event_start,
            :event_end,
            :peak_observed_at,
            :peak_water_level,
            :water_surface_elevation,
            :analysis_datum,
            :analysis_water_surface_elevation,
            :datum,
            :units,
            :sea_level_rise_ft,
            :study_area,
            :method,
            :notes
        )
        ON CONFLICT (scenario_id) DO UPDATE SET
            name = EXCLUDED.name,
            source_station_id = EXCLUDED.source_station_id,
            event_start = EXCLUDED.event_start,
            event_end = EXCLUDED.event_end,
            peak_observed_at = EXCLUDED.peak_observed_at,
            peak_water_level = EXCLUDED.peak_water_level,
            water_surface_elevation = EXCLUDED.water_surface_elevation,
            analysis_datum = EXCLUDED.analysis_datum,
            analysis_water_surface_elevation = EXCLUDED.analysis_water_surface_elevation,
            datum = EXCLUDED.datum,
            units = EXCLUDED.units,
            sea_level_rise_ft = EXCLUDED.sea_level_rise_ft,
            study_area = EXCLUDED.study_area,
            method = EXCLUDED.method,
            notes = EXCLUDED.notes
        """
    )
    with engine.begin() as conn:
        for sea_level_rise_ft in sea_level_rise_values:
            rise_slug = str(sea_level_rise_ft).replace(".", "p")
            scenario_id = f"{study_area_slug}_{peak.station_id}_{event_slug}_plus_{rise_slug}ft"
            scenario_name = f"{study_area} peak water level + {sea_level_rise_ft:g} ft SLR"
            conn.execute(
                query,
                {
                    "scenario_id": scenario_id,
                    "name": scenario_name,
                    "source_station_id": peak.station_id,
                    "event_start": peak.event_start,
                    "event_end": peak.event_end,
                    "peak_observed_at": peak.peak_observed_at,
                    "peak_water_level": peak.peak_water_level,
                    "water_surface_elevation": peak.peak_water_level + sea_level_rise_ft,
                    "analysis_datum": None,
                    "analysis_water_surface_elevation": None,
                    "datum": peak.datum,
                    "units": peak.units,
                    "sea_level_rise_ft": sea_level_rise_ft,
                    "study_area": study_area,
                    "method": method,
                    "notes": notes,
                },
            )
            scenario_ids.append(scenario_id)
    return scenario_ids
