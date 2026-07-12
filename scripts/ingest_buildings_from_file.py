from __future__ import annotations

import argparse
from pathlib import Path

from flood_analysis.db import get_engine, init_db
from flood_analysis.exposure import upsert_file_buildings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest local building footprints clipped to a study area.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    parser.add_argument("--input", type=Path, required=True, help="GeoParquet, GeoPackage, GeoJSON, or Shapefile path.")
    parser.add_argument("--layer", help="Optional layer name for multi-layer files such as GeoPackage.")
    parser.add_argument("--id-column", help="Optional stable building ID column. Defaults to generated file index IDs.")
    parser.add_argument("--name-column", help="Optional building/name column.")
    parser.add_argument("--type-column", help="Optional building type/use column.")
    parser.add_argument("--source", default="Local building footprint file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    count = upsert_file_buildings(
        engine=engine,
        study_area_id=args.study_area_id,
        input_path=args.input,
        layer=args.layer,
        id_column=args.id_column,
        name_column=args.name_column,
        type_column=args.type_column,
        source=args.source,
    )
    print(f"ingested_buildings={count}")


if __name__ == "__main__":
    main()
