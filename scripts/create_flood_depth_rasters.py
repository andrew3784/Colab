from __future__ import annotations

import argparse
from pathlib import Path

from flood_analysis.db import get_engine, init_db
from flood_analysis.rasters import flood_depth_from_dem, navd88_scenarios, register_depth_raster


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create flood-depth GeoTIFFs from a NAVD88 DEM and NAVD88 scenarios.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    parser.add_argument("--dem", type=Path, required=True)
    parser.add_argument("--dem-units", choices=["meters", "feet"], default="meters")
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/flood_depths"))
    parser.add_argument("--scenario-id", action="append", help="Limit processing to one scenario. Repeat for multiple.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    scenarios = navd88_scenarios(engine, args.scenario_id)
    if not scenarios:
        raise RuntimeError("No NAVD88 scenarios found. Run scripts/convert_scenarios_datum.py first.")
    for scenario in scenarios:
        scenario_id = scenario["scenario_id"]
        output_path = args.output_dir / f"{scenario_id}_depth_ft.tif"
        wse_ft = float(scenario["analysis_water_surface_elevation"])
        stats = flood_depth_from_dem(args.dem, output_path, wse_ft, args.dem_units)
        register_depth_raster(
            engine=engine,
            scenario_id=scenario_id,
            study_area_id=args.study_area_id,
            dem_path=args.dem,
            raster_path=output_path,
            water_surface_elevation_ft=wse_ft,
            analysis_datum=scenario["analysis_datum"],
            stats=stats,
        )
        print(
            f"scenario_id={scenario_id} raster={output_path} "
            f"wet_pixels={stats.wet_pixel_count} max_depth_ft={stats.max_depth_ft}"
        )


if __name__ == "__main__":
    main()
