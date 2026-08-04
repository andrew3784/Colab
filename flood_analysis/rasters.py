from __future__ import annotations

import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
import rasterio
import requests
from rasterio.features import shapes
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds, transform_geom
from scipy import ndimage
from shapely.geometry import shape
from shapely.ops import unary_union
from sqlalchemy import text
from sqlalchemy.engine import Engine


TNM_PRODUCTS_URL = "https://tnmaccess.nationalmap.gov/api/v1/products"
DEFAULT_DEM_DATASET = "National Elevation Dataset (NED) 1 arc-second"
METERS_TO_FEET = 3.280839895013123
DEPTH_NODATA = -9999.0


@dataclass(frozen=True)
class DepthStats:
    min_depth_ft: float | None
    max_depth_ft: float | None
    mean_depth_ft: float | None
    wet_pixel_count: int


@dataclass(frozen=True)
class ConnectedDepthStats:
    depth_stats: DepthStats
    removed_wet_pixel_count: int


def read_study_area(engine: Engine, study_area_id: str) -> gpd.GeoDataFrame:
    query = text(
        """
        SELECT study_area_id, name, ST_AsEWKB(geom) AS geom
        FROM processed.study_areas
        WHERE study_area_id = :study_area_id
        """
    )
    gdf = gpd.read_postgis(query, engine, geom_col="geom", params={"study_area_id": study_area_id})
    if gdf.empty:
        raise RuntimeError(f"Study area {study_area_id!r} was not found in processed.study_areas")
    return gdf.set_crs(4326, allow_override=True)


def search_tnm_dem_products(
    engine: Engine,
    study_area_id: str,
    dataset: str = DEFAULT_DEM_DATASET,
    max_results: int = 20,
    product_formats: str = "GeoTIFF",
) -> list[dict]:
    bounds = read_study_area(engine, study_area_id).total_bounds
    params = {
        "datasets": dataset,
        "bbox": ",".join(str(value) for value in bounds),
        "prodFormats": product_formats,
        "max": str(max_results),
        "outputFormat": "JSON",
    }
    response = requests.get(TNM_PRODUCTS_URL, params=params, timeout=60)
    response.raise_for_status()
    return response.json().get("items", [])


def tile_id(product: dict) -> str:
    title = product.get("title", "")
    match = re.search(r"\bn\d+w\d+\b", title.lower())
    return match.group(0) if match else title


def product_sort_key(product: dict) -> tuple[str, str]:
    return (
        product.get("publicationDate") or product.get("lastUpdated") or product.get("dateCreated") or "",
        product.get("title") or "",
    )


def latest_products_per_tile(products: Iterable[dict]) -> list[dict]:
    latest: dict[str, dict] = {}
    for product in products:
        key = tile_id(product)
        if key not in latest or product_sort_key(product) > product_sort_key(latest[key]):
            latest[key] = product
    return sorted(latest.values(), key=lambda item: item.get("title") or "")


def filter_products_by_title(products: Iterable[dict], title_contains: str | None) -> list[dict]:
    if not title_contains:
        return list(products)
    needle = title_contains.lower()
    return [product for product in products if needle in (product.get("title") or "").lower()]


def _download_product(product: dict, output_dir: Path) -> Path:
    url = product.get("downloadURL") or product.get("urls", {}).get("TIFF")
    if not url:
        raise RuntimeError(f"No GeoTIFF download URL found for {product.get('title')}")
    output_path = output_dir / Path(url).name
    expected_size = product.get("sizeInBytes")
    if output_path.exists() and (not expected_size or output_path.stat().st_size == expected_size):
        return output_path

    partial_path = output_path.with_suffix(f"{output_path.suffix}.part")
    offset = partial_path.stat().st_size if partial_path.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with requests.get(url, stream=True, timeout=(30, 600), headers=headers) as response:
        if offset and response.status_code != 206:
            offset = 0
            partial_path.unlink(missing_ok=True)
        response.raise_for_status()
        mode = "ab" if offset else "wb"
        with partial_path.open(mode) as file:
            for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                if chunk:
                    file.write(chunk)
    if expected_size and partial_path.stat().st_size != expected_size:
        raise RuntimeError(
            f"Downloaded size mismatch for {product.get('title')}: "
            f"expected {expected_size}, got {partial_path.stat().st_size}"
        )
    partial_path.replace(output_path)
    return output_path


def download_products(products: Iterable[dict], output_dir: Path, workers: int = 1) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    product_list = list(products)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(lambda product: _download_product(product, output_dir), product_list))


def clip_dem_to_study_area(engine: Engine, study_area_id: str, input_paths: list[Path], output_path: Path) -> None:
    if not input_paths:
        raise RuntimeError("At least one input DEM path is required")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    study_area = read_study_area(engine, study_area_id)
    datasets = [rasterio.open(path) for path in input_paths]
    try:
        if len(datasets) == 1:
            source = datasets[0]
            shapes = [transform_geom(study_area.crs, source.crs, geom.__geo_interface__) for geom in study_area.geometry]
            clipped, transform = mask(source, shapes, crop=True, filled=True)
            profile = source.profile.copy()
        else:
            source_crs = datasets[0].crs
            source_bounds = transform_bounds(study_area.crs, source_crs, *study_area.total_bounds, densify_pts=21)
            profile = datasets[0].profile.copy()
            profile.update(driver="GTiff", compress="deflate", tiled=True, BIGTIFF="IF_SAFER")
            with tempfile.NamedTemporaryFile(suffix=".tif", dir=output_path.parent, delete=False) as tmp_file:
                tmp_path = Path(tmp_file.name)
            try:
                merge(datasets, bounds=source_bounds, dst_path=tmp_path, dst_kwds=profile)
                with rasterio.open(tmp_path) as tmp:
                    shapes = [transform_geom(study_area.crs, tmp.crs, geom.__geo_interface__) for geom in study_area.geometry]
                    clipped, transform = mask(tmp, shapes, crop=True, filled=True)
                    profile = tmp.profile.copy()
            finally:
                tmp_path.unlink(missing_ok=True)
        profile.update(
            driver="GTiff",
            height=clipped.shape[1],
            width=clipped.shape[2],
            transform=transform,
            compress="deflate",
            tiled=True,
            BIGTIFF="IF_SAFER",
        )
        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(clipped)
    finally:
        for dataset in datasets:
            dataset.close()


def flood_depth_from_dem(
    dem_path: Path,
    output_path: Path,
    water_surface_elevation_ft: float,
    dem_units: str = "meters",
) -> DepthStats:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    min_depth: float | None = None
    max_depth: float | None = None
    wet_sum = 0.0
    wet_pixel_count = 0
    with rasterio.open(dem_path) as src:
        if dem_units not in {"meters", "feet"}:
            raise RuntimeError("dem_units must be 'meters' or 'feet'")
        profile = src.profile.copy()
        profile.update(dtype="float32", nodata=DEPTH_NODATA, compress="deflate", tiled=True, BIGTIFF="IF_SAFER")
        with rasterio.open(output_path, "w", **profile) as dst:
            for _, window in src.block_windows(1):
                dem = src.read(1, window=window, masked=True).astype("float32")
                dem_ft = dem * METERS_TO_FEET if dem_units == "meters" else dem
                depth = water_surface_elevation_ft - dem_ft
                depth = np.ma.where(depth > 0, depth, 0.0)
                depth = np.ma.array(depth, mask=np.ma.getmaskarray(dem))
                wet = depth.compressed()
                wet = wet[wet > 0]
                if wet.size:
                    block_min = float(wet.min())
                    block_max = float(wet.max())
                    min_depth = block_min if min_depth is None else min(min_depth, block_min)
                    max_depth = block_max if max_depth is None else max(max_depth, block_max)
                    wet_sum += float(wet.sum(dtype=np.float64))
                    wet_pixel_count += int(wet.size)
                dst.write(depth.filled(DEPTH_NODATA).astype("float32"), 1, window=window)
    stats = DepthStats(
        min_depth_ft=min_depth,
        max_depth_ft=max_depth,
        mean_depth_ft=wet_sum / wet_pixel_count if wet_pixel_count else None,
        wet_pixel_count=wet_pixel_count,
    )
    return stats


def boundary_connected_wet_mask(wet: np.ndarray, valid: np.ndarray) -> np.ndarray:
    if wet.shape != valid.shape:
        raise ValueError("wet and valid masks must have the same shape")
    if wet.ndim != 2:
        raise ValueError("wet and valid masks must be two-dimensional")
    if not wet.any():
        return np.zeros(wet.shape, dtype=bool)

    structure = np.ones((3, 3), dtype=bool)
    seeds = wet & ndimage.binary_dilation(~valid, structure=structure)
    seeds[0, :] |= wet[0, :]
    seeds[-1, :] |= wet[-1, :]
    seeds[:, 0] |= wet[:, 0]
    seeds[:, -1] |= wet[:, -1]

    labels, _ = ndimage.label(wet, structure=structure)
    connected_labels = np.unique(labels[seeds])
    connected_labels = connected_labels[connected_labels != 0]
    return np.isin(labels, connected_labels, assume_unique=True)


def connected_depth_raster(source_path: Path, output_path: Path, min_depth_ft: float = 0.0) -> ConnectedDepthStats:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(source_path) as src:
        valid = np.zeros((src.height, src.width), dtype=bool)
        wet = np.zeros((src.height, src.width), dtype=bool)
        for _, window in src.block_windows(1):
            depth = src.read(1, window=window)
            block_valid = depth != src.nodata if src.nodata is not None else np.ones(depth.shape, dtype=bool)
            valid[window.toslices()] = block_valid
            wet[window.toslices()] = block_valid & (depth > min_depth_ft)
        connected = boundary_connected_wet_mask(wet, valid)
        min_depth: float | None = None
        max_depth: float | None = None
        wet_sum = 0.0
        wet_pixel_count = 0
        profile = src.profile.copy()
        profile.update(dtype="float32", nodata=DEPTH_NODATA, compress="deflate", tiled=True, BIGTIFF="IF_SAFER")
        with rasterio.open(output_path, "w", **profile) as dst:
            for _, window in src.block_windows(1):
                depth = src.read(1, window=window).astype("float32")
                window_connected = connected[window.toslices()]
                window_valid = valid[window.toslices()]
                depth[window_valid & ~window_connected] = 0.0
                wet_values = depth[window_connected]
                if wet_values.size:
                    block_min = float(wet_values.min())
                    block_max = float(wet_values.max())
                    min_depth = block_min if min_depth is None else min(min_depth, block_min)
                    max_depth = block_max if max_depth is None else max(max_depth, block_max)
                    wet_sum += float(wet_values.sum(dtype=np.float64))
                    wet_pixel_count += int(wet_values.size)
                dst.write(depth, 1, window=window)
        stats = DepthStats(
            min_depth_ft=min_depth,
            max_depth_ft=max_depth,
            mean_depth_ft=wet_sum / wet_pixel_count if wet_pixel_count else None,
            wet_pixel_count=wet_pixel_count,
        )
    return ConnectedDepthStats(depth_stats=stats, removed_wet_pixel_count=int(wet.sum() - connected.sum()))


def navd88_scenarios(engine: Engine, scenario_ids: list[str] | None = None) -> list[dict]:
    filters = ["analysis_datum = 'NAVD88'", "analysis_water_surface_elevation IS NOT NULL"]
    params: dict[str, object] = {}
    if scenario_ids:
        filters.append("scenario_id = ANY(:scenario_ids)")
        params["scenario_ids"] = scenario_ids
    query = text(
        f"""
        SELECT scenario_id, name, analysis_water_surface_elevation, analysis_datum
        FROM processed.flood_scenarios
        WHERE {' AND '.join(filters)}
        ORDER BY sea_level_rise_ft, scenario_id
        """
    )
    with engine.connect() as conn:
        return list(conn.execute(query, params).mappings())


def register_depth_raster(
    engine: Engine,
    scenario_id: str,
    study_area_id: str,
    dem_path: Path,
    raster_path: Path,
    water_surface_elevation_ft: float,
    analysis_datum: str,
    stats: DepthStats,
) -> None:
    query = text(
        """
        INSERT INTO results.flood_depth_rasters (
            scenario_id, study_area_id, dem_path, raster_path, water_surface_elevation_ft,
            analysis_datum, min_depth_ft, max_depth_ft, mean_depth_ft, wet_pixel_count
        ) VALUES (
            :scenario_id, :study_area_id, :dem_path, :raster_path, :water_surface_elevation_ft,
            :analysis_datum, :min_depth_ft, :max_depth_ft, :mean_depth_ft, :wet_pixel_count
        )
        ON CONFLICT (scenario_id) DO UPDATE SET
            study_area_id = EXCLUDED.study_area_id,
            dem_path = EXCLUDED.dem_path,
            raster_path = EXCLUDED.raster_path,
            water_surface_elevation_ft = EXCLUDED.water_surface_elevation_ft,
            analysis_datum = EXCLUDED.analysis_datum,
            min_depth_ft = EXCLUDED.min_depth_ft,
            max_depth_ft = EXCLUDED.max_depth_ft,
            mean_depth_ft = EXCLUDED.mean_depth_ft,
            wet_pixel_count = EXCLUDED.wet_pixel_count,
            created_at = now()
        """
    )
    with engine.begin() as conn:
        conn.execute(
            query,
            {
                "scenario_id": scenario_id,
                "study_area_id": study_area_id,
                "dem_path": str(dem_path),
                "raster_path": str(raster_path),
                "water_surface_elevation_ft": water_surface_elevation_ft,
                "analysis_datum": analysis_datum,
                "min_depth_ft": stats.min_depth_ft,
                "max_depth_ft": stats.max_depth_ft,
                "mean_depth_ft": stats.mean_depth_ft,
                "wet_pixel_count": stats.wet_pixel_count,
            },
        )


def registered_depth_rasters(engine: Engine, scenario_ids: list[str] | None = None) -> list[dict]:
    filters = []
    params: dict[str, object] = {}
    if scenario_ids:
        filters.append("scenario_id = ANY(:scenario_ids)")
        params["scenario_ids"] = scenario_ids
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    query = text(
        f"""
        SELECT scenario_id, study_area_id, raster_path
        FROM results.flood_depth_rasters
        {where}
        ORDER BY water_surface_elevation_ft, scenario_id
        """
    )
    with engine.connect() as conn:
        return list(conn.execute(query, params).mappings())


def register_connected_depth_raster(
    engine: Engine,
    scenario_id: str,
    study_area_id: str,
    source_raster_path: Path,
    raster_path: Path,
    stats: ConnectedDepthStats,
    min_depth_threshold_ft: float = 0.0,
) -> None:
    query = text(
        """
        INSERT INTO results.connected_flood_depth_rasters (
            scenario_id, study_area_id, source_raster_path, raster_path, min_depth_threshold_ft,
            min_depth_ft, max_depth_ft, mean_depth_ft, wet_pixel_count, removed_wet_pixel_count
        ) VALUES (
            :scenario_id, :study_area_id, :source_raster_path, :raster_path, :min_depth_threshold_ft,
            :min_depth_ft, :max_depth_ft, :mean_depth_ft, :wet_pixel_count, :removed_wet_pixel_count
        )
        ON CONFLICT (scenario_id) DO UPDATE SET
            study_area_id = EXCLUDED.study_area_id,
            source_raster_path = EXCLUDED.source_raster_path,
            raster_path = EXCLUDED.raster_path,
            min_depth_threshold_ft = EXCLUDED.min_depth_threshold_ft,
            min_depth_ft = EXCLUDED.min_depth_ft,
            max_depth_ft = EXCLUDED.max_depth_ft,
            mean_depth_ft = EXCLUDED.mean_depth_ft,
            wet_pixel_count = EXCLUDED.wet_pixel_count,
            removed_wet_pixel_count = EXCLUDED.removed_wet_pixel_count,
            created_at = now()
        """
    )
    with engine.begin() as conn:
        conn.execute(
            query,
            {
                "scenario_id": scenario_id,
                "study_area_id": study_area_id,
                "source_raster_path": str(source_raster_path),
                "raster_path": str(raster_path),
                "min_depth_threshold_ft": min_depth_threshold_ft,
                "min_depth_ft": stats.depth_stats.min_depth_ft,
                "max_depth_ft": stats.depth_stats.max_depth_ft,
                "mean_depth_ft": stats.depth_stats.mean_depth_ft,
                "wet_pixel_count": stats.depth_stats.wet_pixel_count,
                "removed_wet_pixel_count": stats.removed_wet_pixel_count,
            },
        )


def polygonize_depth_raster(
    engine: Engine,
    scenario_id: str,
    raster_path: Path,
    min_depth_ft: float = 0.0,
    target_resolution: float | None = None,
) -> int:
    with rasterio.open(raster_path) as src:
        transform = src.transform
        if target_resolution and target_resolution > max(abs(src.res[0]), abs(src.res[1])):
            out_width = max(1, int(np.ceil(src.width * abs(src.res[0]) / target_resolution)))
            out_height = max(1, int(np.ceil(src.height * abs(src.res[1]) / target_resolution)))
            depth = src.read(1, out_shape=(out_height, out_width), resampling=Resampling.average)
            transform = src.transform * src.transform.scale(src.width / out_width, src.height / out_height)
        else:
            depth = src.read(1)
        valid = depth != src.nodata if src.nodata is not None else np.ones(depth.shape, dtype=bool)
        wet = valid & (depth > min_depth_ft)
        geometries = [shape(geom) for geom, value in shapes(wet.astype("uint8"), mask=wet, transform=transform) if value == 1]
        if not geometries:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM processed.flood_extents WHERE scenario_id = :scenario_id"), {"scenario_id": scenario_id})
            return 0
        merged = unary_union(geometries)
        if src.crs and src.crs.to_epsg() != 4326:
            merged_geojson = transform_geom(src.crs, "EPSG:4326", merged.__geo_interface__)
            merged = shape(merged_geojson)
        max_depth = float(depth[wet].max())
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM processed.flood_extents WHERE scenario_id = :scenario_id"), {"scenario_id": scenario_id})
        conn.execute(
            text(
                """
                INSERT INTO processed.flood_extents (scenario_id, min_depth_ft, max_depth_ft, geom)
                VALUES (:scenario_id, :min_depth_ft, :max_depth_ft, ST_Multi(ST_MakeValid(ST_GeomFromText(:wkt, 4326))))
                """
            ),
            {
                "scenario_id": scenario_id,
                "min_depth_ft": min_depth_ft,
                "max_depth_ft": max_depth,
                "wkt": merged.wkt,
            },
        )
    return len(geometries)


def polygonize_connected_depth_raster(
    engine: Engine,
    scenario_id: str,
    raster_path: Path,
    min_depth_ft: float = 0.0,
    target_resolution: float | None = None,
) -> int:
    with rasterio.open(raster_path) as src:
        transform = src.transform
        if target_resolution and target_resolution > max(abs(src.res[0]), abs(src.res[1])):
            out_width = max(1, int(np.ceil(src.width * abs(src.res[0]) / target_resolution)))
            out_height = max(1, int(np.ceil(src.height * abs(src.res[1]) / target_resolution)))
            depth = src.read(1, out_shape=(out_height, out_width), resampling=Resampling.average)
            transform = src.transform * src.transform.scale(src.width / out_width, src.height / out_height)
        else:
            depth = src.read(1)
        valid = depth != src.nodata if src.nodata is not None else np.ones(depth.shape, dtype=bool)
        wet = valid & (depth > min_depth_ft)
        geometries = [shape(geom) for geom, value in shapes(wet.astype("uint8"), mask=wet, transform=transform) if value == 1]
        if not geometries:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM processed.connected_flood_extents WHERE scenario_id = :scenario_id"), {"scenario_id": scenario_id})
            return 0
        merged = unary_union(geometries)
        if src.crs and src.crs.to_epsg() != 4326:
            merged_geojson = transform_geom(src.crs, "EPSG:4326", merged.__geo_interface__)
            merged = shape(merged_geojson)
        max_depth = float(depth[wet].max())
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM processed.connected_flood_extents WHERE scenario_id = :scenario_id"), {"scenario_id": scenario_id})
        conn.execute(
            text(
                """
                INSERT INTO processed.connected_flood_extents (scenario_id, min_depth_ft, max_depth_ft, geom)
                VALUES (:scenario_id, :min_depth_ft, :max_depth_ft, ST_Multi(ST_MakeValid(ST_GeomFromText(:wkt, 4326))))
                """
            ),
            {
                "scenario_id": scenario_id,
                "min_depth_ft": min_depth_ft,
                "max_depth_ft": max_depth,
                "wkt": merged.wkt,
            },
        )
    return len(geometries)
