from __future__ import annotations

import argparse
from pathlib import Path

from flood_analysis.db import get_engine, init_db
from flood_analysis.exposure import export_road_impacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export GIS-ready project layers to a GeoPackage.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    parser.add_argument("--output", type=Path, default=Path("data/processed/gis/norfolk_flood_road_exposure.gpkg"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    export_road_impacts(engine, args.study_area_id, args.output)
    print(f"exported={args.output}")


if __name__ == "__main__":
    main()
