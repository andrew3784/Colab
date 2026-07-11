from __future__ import annotations

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union
from sqlalchemy import text
from sqlalchemy.engine import Engine


TIGER_COUNTY_URL = "https://www2.census.gov/geo/tiger/TIGER2023/COUNTY/tl_2023_us_county.zip"


def as_multipolygon(geometry):
    if isinstance(geometry, MultiPolygon):
        return geometry
    if isinstance(geometry, Polygon):
        return MultiPolygon([geometry])
    unioned = unary_union(geometry)
    if isinstance(unioned, Polygon):
        return MultiPolygon([unioned])
    return unioned


def fetch_county_equivalent_boundary(geoid: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(TIGER_COUNTY_URL)
    selected = gdf[gdf["GEOID"] == geoid].copy()
    if selected.empty:
        raise RuntimeError(f"No Census county-equivalent boundary found for GEOID {geoid}")
    return selected.to_crs(4326)


def upsert_study_area_from_tiger_county(
    engine: Engine,
    study_area_id: str,
    geoid: str,
    name: str | None = None,
) -> None:
    boundary = fetch_county_equivalent_boundary(geoid)
    row = boundary.iloc[0]
    geometry = as_multipolygon(row.geometry)
    area_name = name or row["NAMELSAD"]
    query = text(
        """
        INSERT INTO processed.study_areas (study_area_id, name, source, source_id, geom)
        VALUES (:study_area_id, :name, :source, :source_id, ST_Multi(ST_GeomFromText(:wkt, 4326)))
        ON CONFLICT (study_area_id) DO UPDATE SET
            name = EXCLUDED.name,
            source = EXCLUDED.source,
            source_id = EXCLUDED.source_id,
            geom = EXCLUDED.geom
        """
    )
    with engine.begin() as conn:
        conn.execute(
            query,
            {
                "study_area_id": study_area_id,
                "name": area_name,
                "source": "US Census TIGER/Line 2023 County",
                "source_id": geoid,
                "wkt": geometry.wkt,
            },
        )
