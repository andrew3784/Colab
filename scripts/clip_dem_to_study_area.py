from __future__ import annotations

import argparse
from pathlib import Path

from flood_analysis.db import get_engine
from flood_analysis.rasters import clip_dem_to_study_area


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clip one or more DEM GeoTIFFs to a PostGIS study area.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    parser.add_argument("--input-dem", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=Path("data/processed/dem/norfolk_va_usgs_1arcsec_navd88_m.tif"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    clip_dem_to_study_area(get_engine(), args.study_area_id, args.input_dem, args.output)
    print(f"clipped_dem={args.output}")


if __name__ == "__main__":
    main()
