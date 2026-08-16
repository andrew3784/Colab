from __future__ import annotations

import argparse

from flood_analysis.db import get_engine, init_db
from flood_analysis.exposure import upsert_roads


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest TIGER roads clipped to a study area.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    parser.add_argument("--geoid", default="51710", help="County-equivalent GEOID. Default: Norfolk city, VA.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    count = upsert_roads(engine, args.study_area_id, args.geoid)
    print(f"ingested_roads={count}")


if __name__ == "__main__":
    main()
