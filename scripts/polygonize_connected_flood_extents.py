from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import text

from flood_analysis.db import get_engine, init_db
from flood_analysis.rasters import polygonize_connected_depth_raster


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polygonize connected flood-depth rasters into processed.connected_flood_extents.")
    parser.add_argument("--scenario-id", action="append", help="Limit processing to one scenario. Repeat for multiple.")
    parser.add_argument("--min-depth-ft", type=float, default=0.0)
    return parser.parse_args()


def registered_connected_rasters(engine, scenario_ids: list[str] | None) -> list[dict]:
    filters = []
    params: dict[str, object] = {}
    if scenario_ids:
        filters.append("scenario_id = ANY(:scenario_ids)")
        params["scenario_ids"] = scenario_ids
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    query = text(
        f"""
        SELECT scenario_id, raster_path
        FROM results.connected_flood_depth_rasters
        {where}
        ORDER BY wet_pixel_count, scenario_id
        """
    )
    with engine.connect() as conn:
        return list(conn.execute(query, params).mappings())


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    rows = registered_connected_rasters(engine, args.scenario_id)
    if not rows:
        raise RuntimeError("No registered connected flood-depth rasters found. Run scripts/create_connected_flood_depth_rasters.py first.")
    for row in rows:
        polygon_count = polygonize_connected_depth_raster(engine, row["scenario_id"], Path(row["raster_path"]), args.min_depth_ft)
        print(f"scenario_id={row['scenario_id']} source_polygons={polygon_count}")


if __name__ == "__main__":
    main()
