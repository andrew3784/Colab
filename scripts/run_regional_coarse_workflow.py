from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import duckdb
import requests

from flood_analysis.datums import convert_scenarios_to_datum
from flood_analysis.db import get_engine, init_db
from flood_analysis.exposure import (
    calculate_connected_building_flood_impacts,
    calculate_connected_road_flood_impacts,
    upsert_file_buildings,
)
from flood_analysis.impacts import calculate_building_damage_estimates, export_regional_comparison
from flood_analysis.rasters import (
    DEFAULT_DEM_DATASET,
    clip_dem_to_study_area,
    connected_depth_raster,
    download_products,
    filter_products_by_title,
    flood_depth_from_dem,
    latest_products_per_tile,
    navd88_scenarios,
    polygonize_connected_depth_raster,
    polygonize_depth_raster,
    read_study_area,
    register_connected_depth_raster,
    register_depth_raster,
    search_tnm_dem_products,
)
from flood_analysis.scenarios import get_peak_event, upsert_peak_scenarios


REGIONAL_BATCH = {
    "virginia_beach_va": "Virginia Beach pilot",
    "chesapeake_va": "Chesapeake pilot",
    "portsmouth_va": "Portsmouth pilot",
    "suffolk_va": "Suffolk pilot",
    "hampton_va": "Hampton pilot",
    "newport_news_va": "Newport News pilot",
}

ONE_METER_DEM_DATASET = "Digital Elevation Model (DEM) 1 meter"
ONE_METER_DEM_TITLE = "VA_HamptonRoads_B23"
ONE_METER_DEM_URL_TEMPLATE = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/"
    "VA_HamptonRoads_B23/TIFF/USGS_1M_18_x{x}y{y}_VA_HamptonRoads_B23.tif"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the coarse regional flood-impact workflow for selected study areas.")
    parser.add_argument("--study-area-id", action="append", choices=sorted(REGIONAL_BATCH), help="Study area to process. Repeat for multiple.")
    parser.add_argument("--station", default="8638610")
    parser.add_argument("--event-start", default="2026-06-27T00:00:00Z")
    parser.add_argument("--event-end", default="2026-06-28T23:59:59Z")
    parser.add_argument("--datum", default="MLLW")
    parser.add_argument("--units", default="english")
    parser.add_argument("--sea-level-rise-ft", nargs="+", type=float, default=[0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.75, 4.0, 4.5, 5.0, 6.0])
    parser.add_argument("--dem-resolution", choices=["coarse", "1m"], default="coarse")
    parser.add_argument("--dem-dataset", default=DEFAULT_DEM_DATASET)
    parser.add_argument("--dem-product-formats", default="GeoTIFF")
    parser.add_argument("--dem-title-contains", help="Keep only DEM products whose title contains this text.")
    parser.add_argument("--dem-raw-dir", type=Path, default=Path("data/raw/usgs_3dep_regional"))
    parser.add_argument("--dem-output-dir", type=Path, default=Path("data/processed/dem"))
    parser.add_argument("--flood-output-dir", type=Path, default=Path("data/processed/flood_depths_regional_coarse"))
    parser.add_argument("--connected-output-dir", type=Path, default=Path("data/processed/flood_depths_connected_regional_coarse"))
    parser.add_argument("--polygonize-resolution", type=float, help="Optional polygonization resolution in DEM units.")
    parser.add_argument("--overture-release", default="2026-06-17.0")
    parser.add_argument("--overture-output-dir", type=Path, default=Path("data/raw/overture"))
    parser.add_argument("--regional-output", type=Path, default=Path("data/processed/gis/regional_flood_comparison.csv"))
    parser.add_argument("--replacement-cost-per-sqft", type=float, default=175.0)
    parser.add_argument("--download-workers", type=int, default=2)
    parser.add_argument("--refresh-dem", action="store_true", help="Redownload and reclips DEM tiles even if the clipped DEM exists.")
    parser.add_argument("--skip-buildings", action="store_true", help="Only create flood products and road exposure.")
    return parser.parse_args()


def configure_dem_args(args: argparse.Namespace) -> None:
    if args.dem_resolution != "1m":
        return
    if args.dem_dataset == DEFAULT_DEM_DATASET:
        args.dem_dataset = ONE_METER_DEM_DATASET
    if args.dem_product_formats == "GeoTIFF":
        args.dem_product_formats = "GeoTIFF,IMG"
    if args.dem_title_contains is None:
        args.dem_title_contains = ONE_METER_DEM_TITLE
    if args.dem_raw_dir == Path("data/raw/usgs_3dep_regional"):
        args.dem_raw_dir = Path("data/raw/usgs_3dep_1m")
    if args.flood_output_dir == Path("data/processed/flood_depths_regional_coarse"):
        args.flood_output_dir = Path("data/processed/flood_depths_1m")
    if args.connected_output_dir == Path("data/processed/flood_depths_connected_regional_coarse"):
        args.connected_output_dir = Path("data/processed/flood_depths_connected_1m")
    if args.polygonize_resolution is None:
        args.polygonize_resolution = 10.0


def dem_output_path(args: argparse.Namespace, study_area_id: str) -> Path:
    if args.dem_resolution == "1m":
        return args.dem_output_dir / f"{study_area_id}_usgs_1m_hamptonroads_b23_navd88_m.tif"
    return args.dem_output_dir / f"{study_area_id}_usgs_1arcsec_navd88_m.tif"


def discover_hampton_roads_1m_tiles(engine, study_area_id: str) -> list[dict]:
    bounds = read_study_area(engine, study_area_id).to_crs(26918).total_bounds
    left, bottom, right, top = bounds
    products: list[dict] = []
    for x in range(math.floor(left / 10000), math.floor(right / 10000) + 1):
        for y in range(math.floor(bottom / 10000), math.floor(top / 10000) + 1):
            url = ONE_METER_DEM_URL_TEMPLATE.format(x=x, y=y)
            response = requests.head(url, timeout=30)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            size = int(response.headers.get("Content-Length") or 0)
            title = f"USGS 1 Meter 18 x{x}y{y} {ONE_METER_DEM_TITLE}"
            products.append({"title": title, "downloadURL": url, "sizeInBytes": size})
    return products


def scenario_ids_for(study_area_name: str, station: str, event_start: str, sea_level_rise_values: list[float]) -> list[str]:
    event_slug = event_start[:10].replace("-", "")
    area_slug = re.sub(r"[^a-z0-9]+", "_", study_area_name.lower()).strip("_")
    return [f"{area_slug}_{station}_{event_slug}_plus_{str(value).replace('.', 'p')}ft" for value in sea_level_rise_values]


def download_overture_buildings(engine, study_area_id: str, release: str, output_path: Path) -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", release):
        raise RuntimeError("Release must use the Overture YYYY-MM-DD.N format")
    west, south, east, north = read_study_area(engine, study_area_id).total_bounds
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    source = f"s3://overturemaps-us-west-2/release/{release}/theme=buildings/type=building/*"
    output = str(output_path).replace("'", "''")
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
        connection.execute("SET s3_region='us-west-2'")
        connection.execute(
            f"""
            COPY (
                SELECT
                    id,
                    names.primary AS name,
                    subtype AS building_type,
                    geometry
                FROM read_parquet('{source}', filename=true, hive_partitioning=1)
                WHERE bbox.xmin < {east}
                  AND bbox.ymin < {north}
                  AND bbox.xmax > {west}
                  AND bbox.ymax > {south}
            ) TO '{output}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        count = connection.execute("SELECT count(*) FROM read_parquet(?)", [str(output_path)]).fetchone()[0]
    finally:
        connection.close()
    print(f"downloaded_buildings={output_path} candidate_buildings={count}")
    return output_path


def process_study_area(engine, args: argparse.Namespace, study_area_id: str) -> None:
    study_area_name = REGIONAL_BATCH[study_area_id]
    print(f"processing={study_area_id}")

    peak = get_peak_event(engine, args.station, args.event_start, args.event_end, args.datum, args.units)
    scenario_ids = upsert_peak_scenarios(
        engine,
        peak,
        study_area_name,
        args.sea_level_rise_ft,
        f"{args.dem_resolution} regional bathtub screening; connected flood extents; Sewells Point datum conversion",
        f"Regional {args.dem_resolution} workflow for Hampton Roads expansion batch.",
    )
    convert_scenarios_to_datum(engine, args.station, args.datum, "NAVD88")
    print(f"scenarios={','.join(scenario_ids)}")

    dem_path = dem_output_path(args, study_area_id)
    if dem_path.exists() and not args.refresh_dem:
        print(f"clipped_dem={dem_path} reused=true")
    else:
        products = search_tnm_dem_products(engine, study_area_id, args.dem_dataset, 100, args.dem_product_formats)
        products = filter_products_by_title(products, args.dem_title_contains)
        products = latest_products_per_tile(products)
        if not products and args.dem_resolution == "1m" and args.dem_title_contains == ONE_METER_DEM_TITLE:
            products = discover_hampton_roads_1m_tiles(engine, study_area_id)
        if not products:
            raise RuntimeError(f"No DEM products found for {study_area_id} using dataset {args.dem_dataset!r}")
        total_bytes = sum(product.get("sizeInBytes") or 0 for product in products)
        print(f"dem_products={len(products)} total_bytes={total_bytes} dataset={args.dem_dataset!r}")
        dem_paths = download_products(products, args.dem_raw_dir, workers=args.download_workers)
        clip_dem_to_study_area(engine, study_area_id, dem_paths, dem_path)
        print(f"clipped_dem={dem_path}")

    scenarios = {row["scenario_id"]: row for row in navd88_scenarios(engine, scenario_ids)}
    for scenario_id in scenario_ids:
        scenario = scenarios[scenario_id]
        depth_path = args.flood_output_dir / study_area_id / f"{scenario_id}_depth_ft.tif"
        depth_stats = flood_depth_from_dem(dem_path, depth_path, float(scenario["analysis_water_surface_elevation"]), "meters")
        register_depth_raster(engine, scenario_id, study_area_id, dem_path, depth_path, float(scenario["analysis_water_surface_elevation"]), scenario["analysis_datum"], depth_stats)
        polygon_count = polygonize_depth_raster(engine, scenario_id, depth_path, min_depth_ft=0.0, target_resolution=args.polygonize_resolution)
        connected_path = args.connected_output_dir / study_area_id / f"{scenario_id}_connected_depth_ft.tif"
        connected_stats = connected_depth_raster(depth_path, connected_path, min_depth_ft=0.0)
        register_connected_depth_raster(engine, scenario_id, study_area_id, depth_path, connected_path, connected_stats, min_depth_threshold_ft=0.0)
        connected_polygon_count = polygonize_connected_depth_raster(
            engine,
            scenario_id,
            connected_path,
            min_depth_ft=0.0,
            target_resolution=args.polygonize_resolution,
        )
        print(
            f"scenario_id={scenario_id} wet_pixels={depth_stats.wet_pixel_count} "
            f"connected_wet_pixels={connected_stats.depth_stats.wet_pixel_count} "
            f"polygons={polygon_count} connected_polygons={connected_polygon_count}"
        )

    road_rows = calculate_connected_road_flood_impacts(engine, study_area_id)
    print(f"road_exposure_rows={len(road_rows)} study_area_id={study_area_id}")

    if not args.skip_buildings:
        buildings_path = args.overture_output_dir / f"{study_area_id}_buildings.parquet"
        download_overture_buildings(engine, study_area_id, args.overture_release, buildings_path)
        building_count = upsert_file_buildings(
            engine,
            study_area_id,
            buildings_path,
            id_column="id",
            name_column="name",
            type_column="building_type",
            source=f"Overture Maps {args.overture_release}",
        )
        print(f"ingested_buildings={building_count} study_area_id={study_area_id}")
        building_rows = calculate_connected_building_flood_impacts(engine, study_area_id)
        print(f"building_exposure_rows={len(building_rows)} study_area_id={study_area_id}")
        damage_rows = calculate_building_damage_estimates(engine, study_area_id, args.replacement_cost_per_sqft)
        print(f"damage_summary_rows={len(damage_rows)} study_area_id={study_area_id}")


def main() -> None:
    args = parse_args()
    configure_dem_args(args)
    engine = get_engine()
    init_db(engine)
    study_area_ids = args.study_area_id or list(REGIONAL_BATCH)
    for study_area_id in study_area_ids:
        process_study_area(engine, args, study_area_id)
    frame = export_regional_comparison(engine, args.regional_output)
    print(f"exported={args.regional_output} rows={len(frame)}")


if __name__ == "__main__":
    main()
