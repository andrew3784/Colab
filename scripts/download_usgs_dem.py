from __future__ import annotations

import argparse
from pathlib import Path

from flood_analysis.db import get_engine
from flood_analysis.rasters import (
    DEFAULT_DEM_DATASET,
    download_products,
    filter_products_by_title,
    latest_products_per_tile,
    search_tnm_dem_products,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search or download USGS 3DEP DEM GeoTIFFs for a study area.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    parser.add_argument("--dataset", default=DEFAULT_DEM_DATASET)
    parser.add_argument("--product-formats", default="GeoTIFF")
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw/usgs_3dep"))
    parser.add_argument("--title-contains", help="Keep only products whose title contains this text.")
    parser.add_argument("--workers", type=int, default=1, help="Number of concurrent downloads.")
    parser.add_argument("--download", action="store_true", help="Download the latest GeoTIFF for each intersecting tile.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    products = search_tnm_dem_products(get_engine(), args.study_area_id, args.dataset, args.max_results, args.product_formats)
    filtered = filter_products_by_title(products, args.title_contains)
    latest = latest_products_per_tile(filtered)
    if not latest:
        raise RuntimeError("No DEM products matched the requested study area and filters.")
    total_bytes = sum(product.get("sizeInBytes") or 0 for product in latest)
    print(f"product_count={len(latest)} total_bytes={total_bytes}")
    for product in latest:
        print(f"{product.get('title')} | {product.get('sizeInBytes')} bytes | {product.get('downloadURL')}")
    if args.download:
        paths = download_products(latest, args.output_dir, args.workers)
        for path in paths:
            print(f"downloaded={path}")


if __name__ == "__main__":
    main()
