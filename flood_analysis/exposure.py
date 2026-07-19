from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import rasterio
import requests
from rasterio.mask import mask as raster_mask
from rasterio.warp import transform_geom
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.validation import make_valid
from shapely.ops import unary_union
from sqlalchemy import text
from sqlalchemy.engine import Engine

from flood_analysis.rasters import read_study_area


TIGER_ROADS_URL_TEMPLATE = "https://www2.census.gov/geo/tiger/TIGER2023/ROADS/tl_2023_{geoid}_roads.zip"
OVERPASS_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
METERS_PER_MILE = 1609.344


def as_multiline(geometry):
    if isinstance(geometry, MultiLineString):
        return geometry
    if isinstance(geometry, LineString):
        return MultiLineString([geometry])
    unioned = unary_union([part for part in getattr(geometry, "geoms", []) if isinstance(part, LineString)])
    if isinstance(unioned, LineString):
        return MultiLineString([unioned])
    return unioned


def as_multipolygon(geometry):
    if isinstance(geometry, MultiPolygon):
        return geometry
    if isinstance(geometry, Polygon):
        return MultiPolygon([geometry])
    polygons = [part for part in getattr(geometry, "geoms", []) if isinstance(part, Polygon)]
    unioned = unary_union(polygons)
    if isinstance(unioned, Polygon):
        return MultiPolygon([unioned])
    return unioned


def fetch_tiger_roads(geoid: str) -> gpd.GeoDataFrame:
    return gpd.read_file(TIGER_ROADS_URL_TEMPLATE.format(geoid=geoid)).to_crs(4326)


def tiger_roads_for_study_area(engine: Engine, study_area_id: str, geoid: str) -> gpd.GeoDataFrame:
    roads = fetch_tiger_roads(geoid)
    study_area = read_study_area(engine, study_area_id).rename_geometry("geometry")
    clipped = gpd.overlay(roads, study_area[["geometry"]], how="intersection", keep_geom_type=True)
    clipped = clipped[~clipped.geometry.is_empty].copy()
    if clipped.empty:
        raise RuntimeError(f"No TIGER roads intersect study area {study_area_id!r}")
    rows = []
    for linearid, group in clipped.groupby("LINEARID", dropna=False):
        first = group.iloc[0]
        geometry = as_multiline(unary_union(list(group.geometry)))
        if geometry.is_empty:
            continue
        rows.append(
            {
                "road_id": linearid,
                "fullname": first.get("FULLNAME"),
                "rttyp": first.get("RTTYP"),
                "mtfcc": first.get("MTFCC"),
                "geometry": geometry,
            }
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)


def upsert_roads(engine: Engine, study_area_id: str, geoid: str) -> int:
    roads = tiger_roads_for_study_area(engine, study_area_id, geoid)
    query = text(
        """
        INSERT INTO processed.roads (study_area_id, road_id, fullname, rttyp, mtfcc, source, source_id, geom)
        VALUES (:study_area_id, :road_id, :fullname, :rttyp, :mtfcc, :source, :source_id, ST_Multi(ST_GeomFromText(:wkt, 4326)))
        ON CONFLICT (study_area_id, road_id) DO UPDATE SET
            fullname = EXCLUDED.fullname,
            rttyp = EXCLUDED.rttyp,
            mtfcc = EXCLUDED.mtfcc,
            source = EXCLUDED.source,
            source_id = EXCLUDED.source_id,
            geom = EXCLUDED.geom
        """
    )
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM processed.roads WHERE study_area_id = :study_area_id"), {"study_area_id": study_area_id})
        for row in roads.itertuples(index=False):
            conn.execute(
                query,
                {
                    "study_area_id": study_area_id,
                    "road_id": row.road_id,
                    "fullname": row.fullname,
                    "rttyp": row.rttyp,
                    "mtfcc": row.mtfcc,
                    "source": "US Census TIGER/Line 2023 Roads",
                    "source_id": geoid,
                    "wkt": row.geometry.wkt,
                },
            )
    return len(roads)


def fetch_osm_buildings(bounds: tuple[float, float, float, float], timeout_seconds: int = 180) -> gpd.GeoDataFrame:
    west, south, east, north = bounds
    query = f"""
    [out:json][timeout:{timeout_seconds}];
    way["building"]({south},{west},{north},{east});
    out geom tags;
    """
    last_error: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            response = requests.get(
                url,
                params={"data": query},
                timeout=timeout_seconds + 30,
                headers={"User-Agent": "odu-flood-analysis/0.1"},
            )
            response.raise_for_status()
            break
        except Exception as exc:
            last_error = exc
    else:
        raise RuntimeError(f"Overpass building request failed for bounds {bounds}: {last_error}")
    rows = []
    for element in response.json().get("elements", []):
        coords = [(point["lon"], point["lat"]) for point in element.get("geometry", [])]
        if len(coords) < 4:
            continue
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        geometry = make_valid(Polygon(coords))
        geometry = as_multipolygon(geometry)
        if geometry.is_empty:
            continue
        tags = element.get("tags", {})
        rows.append(
            {
                "building_id": f"osm_way_{element['id']}",
                "name": tags.get("name"),
                "building_type": tags.get("building"),
                "geometry": geometry,
            }
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)


def split_bounds(bounds, rows: int = 8, cols: int = 8) -> list[tuple[float, float, float, float]]:
    west, south, east, north = bounds
    width = (east - west) / cols
    height = (north - south) / rows
    chunks = []
    for row in range(rows):
        for col in range(cols):
            chunks.append(
                (
                    west + col * width,
                    south + row * height,
                    west + (col + 1) * width,
                    south + (row + 1) * height,
                )
            )
    return chunks


def fetch_osm_buildings_chunked(bounds, timeout_seconds: int = 60, max_depth: int = 2) -> list[gpd.GeoDataFrame]:
    try:
        frame = fetch_osm_buildings(bounds, timeout_seconds=timeout_seconds)
        return [frame] if not frame.empty else []
    except Exception:
        if max_depth <= 0:
            raise
        frames = []
        for child_bounds in split_bounds(bounds, rows=2, cols=2):
            frames.extend(fetch_osm_buildings_chunked(child_bounds, timeout_seconds=timeout_seconds, max_depth=max_depth - 1))
        return frames


def osm_buildings_for_study_area(engine: Engine, study_area_id: str) -> gpd.GeoDataFrame:
    study_area = read_study_area(engine, study_area_id).rename_geometry("geometry")
    frames = []
    for bounds in split_bounds(study_area.total_bounds):
        frames.extend(fetch_osm_buildings_chunked(bounds, timeout_seconds=60, max_depth=2))
    if not frames:
        raise RuntimeError("No OSM building footprints returned by Overpass")
    buildings = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True).drop_duplicates(subset=["building_id"]),
        geometry="geometry",
        crs=4326,
    )
    clipped = gpd.overlay(buildings, study_area[["geometry"]], how="intersection", keep_geom_type=True)
    clipped = clipped[~clipped.geometry.is_empty].copy()
    if clipped.empty:
        raise RuntimeError(f"No OSM buildings intersect study area {study_area_id!r}")
    rows = []
    for building_id, group in clipped.groupby("building_id", dropna=False):
        first = group.iloc[0]
        geometry = as_multipolygon(make_valid(unary_union(list(group.geometry))))
        if geometry.is_empty:
            continue
        rows.append(
            {
                "building_id": building_id,
                "name": first.get("name"),
                "building_type": first.get("building_type"),
                "geometry": geometry,
            }
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)


def upsert_osm_buildings(engine: Engine, study_area_id: str) -> int:
    buildings = osm_buildings_for_study_area(engine, study_area_id)
    return upsert_buildings(engine, study_area_id, buildings, "OpenStreetMap via Overpass", "building ways")


def file_buildings_for_study_area(
    engine: Engine,
    study_area_id: str,
    input_path: Path,
    layer: str | None = None,
    id_column: str | None = None,
    name_column: str | None = None,
    type_column: str | None = None,
) -> gpd.GeoDataFrame:
    if input_path.suffix.lower() in {".parquet", ".geoparquet"}:
        source = gpd.read_parquet(input_path)
    else:
        kwargs = {"layer": layer} if layer else {}
        source = gpd.read_file(input_path, **kwargs)
    if source.crs is None:
        raise RuntimeError("Input building footprint file has no CRS. Define a CRS before ingesting.")
    source = source.to_crs(4326)
    study_area = read_study_area(engine, study_area_id).rename_geometry("geometry")
    clipped = gpd.overlay(source, study_area[["geometry"]], how="intersection", keep_geom_type=True)
    clipped = clipped[~clipped.geometry.is_empty].copy()
    if clipped.empty:
        raise RuntimeError(f"No input buildings intersect study area {study_area_id!r}")
    rows = []
    for index, row in clipped.reset_index(drop=True).iterrows():
        geometry = as_multipolygon(make_valid(row.geometry))
        if geometry.is_empty:
            continue
        rows.append(
            {
                "building_id": str(row[id_column]) if id_column else f"file_{index}",
                "name": str(row[name_column]) if name_column and pd.notna(row.get(name_column)) else None,
                "building_type": str(row[type_column]) if type_column and pd.notna(row.get(type_column)) else None,
                "geometry": geometry,
            }
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)


def upsert_file_buildings(
    engine: Engine,
    study_area_id: str,
    input_path: Path,
    layer: str | None = None,
    id_column: str | None = None,
    name_column: str | None = None,
    type_column: str | None = None,
    source: str = "Local building footprint file",
) -> int:
    buildings = file_buildings_for_study_area(engine, study_area_id, input_path, layer, id_column, name_column, type_column)
    return upsert_buildings(engine, study_area_id, buildings, source, str(input_path))


def upsert_buildings(engine: Engine, study_area_id: str, buildings: gpd.GeoDataFrame, source: str, source_id: str) -> int:
    query = text(
        """
        INSERT INTO processed.buildings (study_area_id, building_id, name, building_type, source, source_id, geom)
        VALUES (:study_area_id, :building_id, :name, :building_type, :source, :source_id, ST_Multi(ST_GeomFromText(:wkt, 4326)))
        ON CONFLICT (study_area_id, building_id) DO UPDATE SET
            name = EXCLUDED.name,
            building_type = EXCLUDED.building_type,
            source = EXCLUDED.source,
            source_id = EXCLUDED.source_id,
            geom = EXCLUDED.geom
        """
    )
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM processed.buildings WHERE study_area_id = :study_area_id"), {"study_area_id": study_area_id})
        batch = []
        for row in buildings.itertuples(index=False):
            batch.append(
                {
                    "study_area_id": study_area_id,
                    "building_id": row.building_id,
                    "name": row.name,
                    "building_type": row.building_type,
                    "source": source,
                    "source_id": source_id,
                    "wkt": row.geometry.wkt,
                }
            )
            if len(batch) == 5000:
                conn.execute(query, batch)
                batch.clear()
        if batch:
            conn.execute(query, batch)
    return len(buildings)


def calculate_road_flood_impacts(engine: Engine, study_area_id: str) -> list[dict]:
    return calculate_road_flood_impacts_for_extent_source(
        engine,
        study_area_id,
        extent_table="processed.flood_extents",
        impact_table="results.road_flood_impacts",
        summary_table="results.road_exposure_summary",
    )


def calculate_connected_road_flood_impacts(engine: Engine, study_area_id: str) -> list[dict]:
    return calculate_road_flood_impacts_for_extent_source(
        engine,
        study_area_id,
        extent_table="processed.connected_flood_extents",
        impact_table="results.connected_road_flood_impacts",
        summary_table="results.connected_road_exposure_summary",
    )


def calculate_connected_building_flood_impacts(engine: Engine, study_area_id: str) -> list[dict]:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM results.connected_building_flood_impacts WHERE study_area_id = :study_area_id"), {"study_area_id": study_area_id})
        conn.execute(text("DELETE FROM results.connected_building_exposure_summary WHERE study_area_id = :study_area_id"), {"study_area_id": study_area_id})
        conn.execute(
            text(
                """
                WITH intersections AS MATERIALIZED (
                    SELECT
                        e.scenario_id,
                        b.study_area_id,
                        b.building_id,
                        b.name,
                        b.building_type,
                        b.geom AS building_geom,
                        ST_CollectionExtract(ST_Intersection(b.geom, e.geom), 3) AS flooded_geom
                    FROM processed.buildings b
                    JOIN processed.connected_flood_extents e
                      ON ST_Intersects(b.geom, e.geom)
                    JOIN results.connected_flood_depth_rasters r
                      ON r.scenario_id = e.scenario_id
                     AND r.study_area_id = b.study_area_id
                    WHERE b.study_area_id = :study_area_id
                )
                INSERT INTO results.connected_building_flood_impacts (
                    scenario_id, study_area_id, building_id, name, building_type,
                    footprint_area_m2, flooded_area_m2, flooded_area_fraction, max_depth_ft
                )
                SELECT
                    scenario_id,
                    study_area_id,
                    building_id,
                    name,
                    building_type,
                    ST_Area(building_geom::geography) AS footprint_area_m2,
                    ST_Area(flooded_geom::geography) AS flooded_area_m2,
                    ST_Area(flooded_geom::geography) / NULLIF(ST_Area(building_geom::geography), 0) AS flooded_area_fraction,
                    NULL
                FROM intersections
                WHERE NOT ST_IsEmpty(flooded_geom)
                  AND ST_Area(flooded_geom::geography) > 0
                """
            ),
            {"study_area_id": study_area_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO results.connected_building_exposure_summary (
                    scenario_id, study_area_id, building_count, flooded_building_count,
                    total_footprint_area_m2, flooded_footprint_area_m2
                )
                SELECT
                    s.scenario_id,
                    :study_area_id,
                    (SELECT count(*) FROM processed.buildings WHERE study_area_id = :study_area_id),
                    count(i.building_id),
                    (SELECT coalesce(sum(ST_Area(geom::geography)), 0) FROM processed.buildings WHERE study_area_id = :study_area_id),
                    coalesce(sum(i.flooded_area_m2), 0)
                FROM processed.flood_scenarios s
                LEFT JOIN results.connected_building_flood_impacts i
                    ON i.scenario_id = s.scenario_id
                    AND i.study_area_id = :study_area_id
                WHERE EXISTS (
                    SELECT 1
                    FROM results.connected_flood_depth_rasters r
                    WHERE r.scenario_id = s.scenario_id
                      AND r.study_area_id = :study_area_id
                )
                GROUP BY s.scenario_id
                """
            ),
            {"study_area_id": study_area_id},
        )
    sample_connected_building_depths(engine, study_area_id)
    return connected_building_exposure_summary(engine, study_area_id)


def sample_connected_building_depths(engine: Engine, study_area_id: str) -> None:
    raster_query = text(
        """
        SELECT scenario_id, raster_path
        FROM results.connected_flood_depth_rasters
        WHERE study_area_id = :study_area_id
        ORDER BY scenario_id
        """
    )
    with engine.connect() as conn:
        rasters = list(conn.execute(raster_query, {"study_area_id": study_area_id}).mappings())

    update_query = text(
        """
        UPDATE results.connected_building_flood_impacts
        SET max_depth_ft = :max_depth_ft
        WHERE scenario_id = :scenario_id
          AND study_area_id = :study_area_id
          AND building_id = :building_id
        """
    )
    for raster in rasters:
        impacts = gpd.read_postgis(
            text(
                """
                SELECT i.building_id, ST_AsEWKB(b.geom) AS geom
                FROM results.connected_building_flood_impacts i
                JOIN processed.buildings b
                  ON b.study_area_id = i.study_area_id
                 AND b.building_id = i.building_id
                WHERE i.scenario_id = :scenario_id
                  AND i.study_area_id = :study_area_id
                """
            ),
            engine,
            geom_col="geom",
            params={"scenario_id": raster["scenario_id"], "study_area_id": study_area_id},
        ).set_crs(4326, allow_override=True)
        updates = []
        with rasterio.open(raster["raster_path"]) as source:
            for row in impacts.itertuples(index=False):
                geometry = transform_geom(impacts.crs, source.crs, row.geom.__geo_interface__)
                values, _ = raster_mask(source, [geometry], crop=True, filled=False, indexes=1)
                wet = values.compressed()
                wet = wet[wet > 0]
                updates.append(
                    {
                        "scenario_id": raster["scenario_id"],
                        "study_area_id": study_area_id,
                        "building_id": row.building_id,
                        "max_depth_ft": float(wet.max()) if wet.size else None,
                    }
                )
        with engine.begin() as conn:
            for start in range(0, len(updates), 5000):
                conn.execute(update_query, updates[start : start + 5000])
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DELETE FROM results.connected_building_flood_impacts
                WHERE study_area_id = :study_area_id
                  AND max_depth_ft IS NULL
                """
            ),
            {"study_area_id": study_area_id},
        )
        conn.execute(
            text(
                """
                UPDATE results.connected_building_exposure_summary s
                SET flooded_building_count = totals.building_count,
                    flooded_footprint_area_m2 = totals.flooded_area_m2,
                    created_at = now()
                FROM (
                    SELECT scenario_id, count(*) AS building_count,
                           coalesce(sum(flooded_area_m2), 0) AS flooded_area_m2
                    FROM results.connected_building_flood_impacts
                    WHERE study_area_id = :study_area_id
                    GROUP BY scenario_id
                ) totals
                WHERE s.study_area_id = :study_area_id
                  AND s.scenario_id = totals.scenario_id
                """
            ),
            {"study_area_id": study_area_id},
        )


def connected_building_exposure_summary(engine: Engine, study_area_id: str) -> list[dict]:
    query = text(
        """
        SELECT scenario_id, building_count, flooded_building_count, total_footprint_area_m2, flooded_footprint_area_m2
        FROM results.connected_building_exposure_summary
        WHERE study_area_id = :study_area_id
        ORDER BY flooded_footprint_area_m2, scenario_id
        """
    )
    with engine.connect() as conn:
        return list(conn.execute(query, {"study_area_id": study_area_id}).mappings())


def calculate_road_flood_impacts_for_extent_source(
    engine: Engine,
    study_area_id: str,
    extent_table: str,
    impact_table: str,
    summary_table: str,
) -> list[dict]:
    if extent_table not in {"processed.flood_extents", "processed.connected_flood_extents"}:
        raise RuntimeError(f"Unsupported extent table {extent_table}")
    if impact_table not in {"results.road_flood_impacts", "results.connected_road_flood_impacts"}:
        raise RuntimeError(f"Unsupported impact table {impact_table}")
    if summary_table not in {"results.road_exposure_summary", "results.connected_road_exposure_summary"}:
        raise RuntimeError(f"Unsupported summary table {summary_table}")
    raster_table = "results.connected_flood_depth_rasters" if extent_table == "processed.connected_flood_extents" else "results.flood_depth_rasters"
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {impact_table} WHERE study_area_id = :study_area_id"), {"study_area_id": study_area_id})
        conn.execute(text(f"DELETE FROM {summary_table} WHERE study_area_id = :study_area_id"), {"study_area_id": study_area_id})
        conn.execute(
            text(
                f"""
                INSERT INTO {impact_table} (
                    scenario_id, study_area_id, road_id, fullname, mtfcc,
                    flooded_length_m, flooded_length_mi, max_depth_ft
                )
                SELECT
                    e.scenario_id,
                    r.study_area_id,
                    r.road_id,
                    r.fullname,
                    r.mtfcc,
                    ST_Length(ST_CollectionExtract(ST_Intersection(r.geom, e.geom), 2)::geography) AS flooded_length_m,
                    ST_Length(ST_CollectionExtract(ST_Intersection(r.geom, e.geom), 2)::geography) / :meters_per_mile AS flooded_length_mi,
                    e.max_depth_ft
                FROM processed.roads r
                JOIN {extent_table} e
                    ON ST_Intersects(r.geom, e.geom)
                JOIN {raster_table} fr
                    ON fr.scenario_id = e.scenario_id
                    AND fr.study_area_id = r.study_area_id
                WHERE r.study_area_id = :study_area_id
                    AND NOT ST_IsEmpty(ST_Intersection(r.geom, e.geom))
                    AND ST_Length(ST_CollectionExtract(ST_Intersection(r.geom, e.geom), 2)::geography) > 0
                """
            ),
            {"study_area_id": study_area_id, "meters_per_mile": METERS_PER_MILE},
        )
        conn.execute(
            text(
                f"""
                INSERT INTO {summary_table} (
                    scenario_id, study_area_id, road_count, flooded_road_count,
                    total_road_length_m, flooded_length_m, flooded_length_mi
                )
                SELECT
                    s.scenario_id,
                    :study_area_id,
                    (SELECT count(*) FROM processed.roads WHERE study_area_id = :study_area_id),
                    count(i.road_id),
                    (SELECT coalesce(sum(ST_Length(geom::geography)), 0) FROM processed.roads WHERE study_area_id = :study_area_id),
                    coalesce(sum(i.flooded_length_m), 0),
                    coalesce(sum(i.flooded_length_mi), 0)
                FROM processed.flood_scenarios s
                LEFT JOIN {impact_table} i
                    ON i.scenario_id = s.scenario_id
                    AND i.study_area_id = :study_area_id
                WHERE EXISTS (
                    SELECT 1
                    FROM {raster_table} fr
                    WHERE fr.scenario_id = s.scenario_id
                      AND fr.study_area_id = :study_area_id
                )
                GROUP BY s.scenario_id
                """
            ),
            {"study_area_id": study_area_id},
        )
    return road_exposure_summary(engine, study_area_id, summary_table)


def road_exposure_summary(
    engine: Engine,
    study_area_id: str,
    summary_table: str = "results.road_exposure_summary",
) -> list[dict]:
    if summary_table not in {"results.road_exposure_summary", "results.connected_road_exposure_summary"}:
        raise RuntimeError(f"Unsupported summary table {summary_table}")
    query = text(
        f"""
        SELECT scenario_id, road_count, flooded_road_count, total_road_length_m, flooded_length_m, flooded_length_mi
        FROM {summary_table}
        WHERE study_area_id = :study_area_id
        ORDER BY flooded_length_m, scenario_id
        """
    )
    with engine.connect() as conn:
        return list(conn.execute(query, {"study_area_id": study_area_id}).mappings())


def export_road_impacts(engine: Engine, study_area_id: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    roads = gpd.read_postgis(
        text(
            """
            SELECT road_id, fullname, mtfcc, ST_AsEWKB(geom) AS geom
            FROM processed.roads
            WHERE study_area_id = :study_area_id
            """
        ),
        engine,
        geom_col="geom",
        params={"study_area_id": study_area_id},
    ).set_crs(4326, allow_override=True)
    extents = gpd.read_postgis(
        text(
            """
            SELECT e.scenario_id, e.min_depth_ft, e.max_depth_ft, ST_AsEWKB(e.geom) AS geom
            FROM processed.flood_extents e
            JOIN results.flood_depth_rasters r
              ON r.scenario_id = e.scenario_id
            WHERE r.study_area_id = :study_area_id
            """
        ),
        engine,
        geom_col="geom",
        params={"study_area_id": study_area_id},
    ).set_crs(4326, allow_override=True)
    connected_extents = gpd.read_postgis(
        text(
            """
            SELECT e.scenario_id, e.min_depth_ft, e.max_depth_ft, ST_AsEWKB(e.geom) AS geom
            FROM processed.connected_flood_extents e
            JOIN results.connected_flood_depth_rasters r
              ON r.scenario_id = e.scenario_id
            WHERE r.study_area_id = :study_area_id
            """
        ),
        engine,
        geom_col="geom",
        params={"study_area_id": study_area_id},
    ).set_crs(4326, allow_override=True)
    impacts = gpd.read_postgis(
        text(
            """
            SELECT i.scenario_id, i.road_id, i.fullname, i.mtfcc, i.flooded_length_m, i.flooded_length_mi,
                   ST_AsEWKB(r.geom) AS geom
            FROM results.road_flood_impacts i
            JOIN processed.roads r
                ON r.study_area_id = i.study_area_id
                AND r.road_id = i.road_id
            WHERE i.study_area_id = :study_area_id
            """
        ),
        engine,
        geom_col="geom",
        params={"study_area_id": study_area_id},
    ).set_crs(4326, allow_override=True)
    connected_impacts = gpd.read_postgis(
        text(
            """
            SELECT i.scenario_id, i.road_id, i.fullname, i.mtfcc, i.flooded_length_m, i.flooded_length_mi,
                   ST_AsEWKB(r.geom) AS geom
            FROM results.connected_road_flood_impacts i
            JOIN processed.roads r
                ON r.study_area_id = i.study_area_id
                AND r.road_id = i.road_id
            WHERE i.study_area_id = :study_area_id
            """
        ),
        engine,
        geom_col="geom",
        params={"study_area_id": study_area_id},
    ).set_crs(4326, allow_override=True)
    buildings = gpd.read_postgis(
        text(
            """
            SELECT building_id, name, building_type, ST_AsEWKB(geom) AS geom
            FROM processed.buildings
            WHERE study_area_id = :study_area_id
            """
        ),
        engine,
        geom_col="geom",
        params={"study_area_id": study_area_id},
    ).set_crs(4326, allow_override=True)
    building_impacts = gpd.read_postgis(
        text(
            """
            SELECT i.scenario_id, i.building_id, i.name, i.building_type,
                   i.footprint_area_m2, i.flooded_area_m2, i.flooded_area_fraction, i.max_depth_ft,
                   ST_AsEWKB(b.geom) AS geom
            FROM results.connected_building_flood_impacts i
            JOIN processed.buildings b
                ON b.study_area_id = i.study_area_id
                AND b.building_id = i.building_id
            WHERE i.study_area_id = :study_area_id
            """
        ),
        engine,
        geom_col="geom",
        params={"study_area_id": study_area_id},
    ).set_crs(4326, allow_override=True)
    building_damage = gpd.read_postgis(
        text(
            """
            SELECT d.scenario_id, d.building_id, d.name, d.building_type,
                   d.max_depth_ft, d.flooded_area_fraction, d.damage_class,
                   d.estimated_damage_fraction, d.estimated_damage_cost,
                   d.recovery_class, d.estimated_recovery_days,
                   ST_AsEWKB(b.geom) AS geom
            FROM results.connected_building_damage_estimates d
            JOIN processed.buildings b
                ON b.study_area_id = d.study_area_id
                AND b.building_id = d.building_id
            WHERE d.study_area_id = :study_area_id
            """
        ),
        engine,
        geom_col="geom",
        params={"study_area_id": study_area_id},
    ).set_crs(4326, allow_override=True)
    if output_path.exists():
        output_path.unlink()
    roads.to_file(output_path, layer="roads", driver="GPKG")
    extents.to_file(output_path, layer="flood_extents", driver="GPKG")
    impacts.to_file(output_path, layer="road_flood_impacts", driver="GPKG")
    if not connected_extents.empty:
        connected_extents.to_file(output_path, layer="connected_flood_extents", driver="GPKG")
    if not connected_impacts.empty:
        connected_impacts.to_file(output_path, layer="connected_road_flood_impacts", driver="GPKG")
    if not buildings.empty:
        buildings.to_file(output_path, layer="buildings", driver="GPKG")
    if not building_impacts.empty:
        building_impacts.to_file(output_path, layer="connected_building_flood_impacts", driver="GPKG")
    if not building_damage.empty:
        building_damage.to_file(output_path, layer="connected_building_damage_estimates", driver="GPKG")
    road_summary = pd.read_sql(
        text("SELECT * FROM results.connected_road_exposure_summary WHERE study_area_id = :study_area_id ORDER BY scenario_id"),
        engine,
        params={"study_area_id": study_area_id},
    )
    building_summary = pd.read_sql(
        text("SELECT * FROM results.connected_building_exposure_summary WHERE study_area_id = :study_area_id ORDER BY scenario_id"),
        engine,
        params={"study_area_id": study_area_id},
    )
    damage_summary = pd.read_sql(
        text("SELECT * FROM results.connected_building_damage_summary WHERE study_area_id = :study_area_id ORDER BY scenario_id"),
        engine,
        params={"study_area_id": study_area_id},
    )
    road_summary.to_csv(output_path.with_name(f"{output_path.stem}_road_summary.csv"), index=False)
    building_summary.to_csv(output_path.with_name(f"{output_path.stem}_building_summary.csv"), index=False)
    damage_summary.to_csv(output_path.with_name(f"{output_path.stem}_damage_summary.csv"), index=False)
