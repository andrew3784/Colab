from __future__ import annotations

import argparse
from pathlib import Path

from flood_analysis.db import get_engine, init_db
from flood_analysis.rasters import connected_depth_raster, register_connected_depth_raster, registered_depth_rasters


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create edge-connected flood-depth rasters from registered bathtub depth rasters.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/flood_depths_connected"))
    parser.add_argument("--min-depth-ft", type=float, default=0.0)
    parser.add_argument("--scenario-id", action="append", help="Limit processing to one scenario. Repeat for multiple.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    rows = registered_depth_rasters(engine, args.scenario_id)
    if not rows:
        raise RuntimeError("No registered flood-depth rasters found. Run scripts/create_flood_depth_rasters.py first.")
    for row in rows:
        source_path = Path(row["raster_path"])
        output_path = args.output_dir / f"{row['scenario_id']}_connected_depth_ft.tif"
        stats = connected_depth_raster(source_path, output_path, args.min_depth_ft)
        register_connected_depth_raster(
            engine,
            row["scenario_id"],
            row["study_area_id"],
            source_path,
            output_path,
            stats,
            args.min_depth_ft,
        )
        print(
            f"scenario_id={row['scenario_id']} raster={output_path} "
            f"wet_pixels={stats.depth_stats.wet_pixel_count} removed_wet_pixels={stats.removed_wet_pixel_count}"
        )


if __name__ == "__main__":
    main()
