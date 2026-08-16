from __future__ import annotations

import argparse

from flood_analysis.db import get_engine, init_db
from flood_analysis.exposure import upsert_osm_buildings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest OSM building footprints clipped to a study area.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    count = upsert_osm_buildings(engine, args.study_area_id)
    print(f"ingested_buildings={count}")


if __name__ == "__main__":
    main()
