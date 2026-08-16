from __future__ import annotations

import requests
from sqlalchemy import text
from sqlalchemy.engine import Engine

from flood_analysis.impacts import REGIONAL_STUDY_AREA_IDS


ACS_VARIABLE = "B25077_001E"
ACS_SOURCE_TEMPLATE = "US Census ACS {year} 5-year table B25077 median owner-occupied home value"
CENSUS_REPORTER_TABLE = "B25077"
CENSUS_REPORTER_FIELD = "B25077001"


def fetch_acs_median_home_values(engine: Engine, year: int = 2022, study_area_ids: list[str] | None = None) -> list[dict]:
    study_area_ids = study_area_ids or REGIONAL_STUDY_AREA_IDS
    with engine.connect() as conn:
        study_areas = list(
            conn.execute(
                text(
                    """
                    SELECT study_area_id, name, source_id AS geoid
                    FROM processed.study_areas
                    WHERE study_area_id = ANY(:study_area_ids)
                    ORDER BY name
                    """
                ),
                {"study_area_ids": study_area_ids},
            ).mappings()
        )

    geo_ids = []
    areas_by_geo_id = {}
    for area in study_areas:
        geoid = area["geoid"]
        if not geoid or len(geoid) != 5 or not geoid.startswith("51"):
            raise RuntimeError(f"Study area {area['study_area_id']} does not have a Virginia county-equivalent GEOID")
        geo_id = f"05000US{geoid}"
        geo_ids.append(geo_id)
        areas_by_geo_id[geo_id] = area

    response = requests.get(
        "https://api.censusreporter.org/1.0/data/show/latest",
        params={"table_ids": CENSUS_REPORTER_TABLE, "geo_ids": ",".join(geo_ids)},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    release_name = data.get("release", {}).get("name") or ACS_SOURCE_TEMPLATE.format(year=year)

    rows = []
    for geo_id in geo_ids:
        area = areas_by_geo_id[geo_id]
        estimates = data.get("data", {}).get(geo_id, {}).get(CENSUS_REPORTER_TABLE, {}).get("estimate", {})
        raw_value = estimates.get(CENSUS_REPORTER_FIELD)
        median_home_value = None if raw_value is None else float(raw_value)
        geography_name = data.get("geography", {}).get(geo_id, {}).get("name") or area["name"]
        rows.append(
            {
                "study_area_id": area["study_area_id"],
                "geoid": area["geoid"],
                "year": year,
                "name": geography_name,
                "median_home_value": median_home_value,
                "source": f"{release_name}; Census Reporter table {CENSUS_REPORTER_TABLE}",
            }
        )

    query = text(
        """
        INSERT INTO processed.acs_median_home_values (
            study_area_id, geoid, year, name, median_home_value, source
        ) VALUES (
            :study_area_id, :geoid, :year, :name, :median_home_value, :source
        )
        ON CONFLICT (study_area_id, year) DO UPDATE SET
            geoid = EXCLUDED.geoid,
            name = EXCLUDED.name,
            median_home_value = EXCLUDED.median_home_value,
            source = EXCLUDED.source,
            created_at = now()
        """
    )
    with engine.begin() as conn:
        conn.execute(query, rows)
    return rows


def calculate_property_value_exposure(engine: Engine, year: int = 2022, study_area_ids: list[str] | None = None) -> list[dict]:
    study_area_ids = study_area_ids or REGIONAL_STUDY_AREA_IDS
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DELETE FROM results.property_value_exposure_summary
                WHERE acs_year = :year
                  AND study_area_id = ANY(:study_area_ids)
                """
            ),
            {"year": year, "study_area_ids": study_area_ids},
        )
        conn.execute(
            text(
                """
                INSERT INTO results.property_value_exposure_summary (
                    scenario_id, study_area_id, acs_year, median_home_value,
                    flooded_building_count, estimated_exposed_property_value
                )
                SELECT
                    i.scenario_id,
                    i.study_area_id,
                    v.year,
                    v.median_home_value,
                    count(*),
                    coalesce(sum(v.median_home_value * i.flooded_area_fraction), 0)
                FROM results.connected_building_flood_impacts i
                JOIN processed.acs_median_home_values v
                  ON v.study_area_id = i.study_area_id
                 AND v.year = :year
                WHERE i.study_area_id = ANY(:study_area_ids)
                  AND v.median_home_value IS NOT NULL
                GROUP BY i.scenario_id, i.study_area_id, v.year, v.median_home_value
                """
            ),
            {"year": year, "study_area_ids": study_area_ids},
        )

    query = text(
        """
        SELECT scenario_id, study_area_id, acs_year, median_home_value,
               flooded_building_count, estimated_exposed_property_value
        FROM results.property_value_exposure_summary
        WHERE acs_year = :year
          AND study_area_id = ANY(:study_area_ids)
        ORDER BY estimated_exposed_property_value, scenario_id
        """
    )
    with engine.connect() as conn:
        return list(conn.execute(query, {"year": year, "study_area_ids": study_area_ids}).mappings())
