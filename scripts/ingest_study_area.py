from __future__ import annotations

import argparse

from flood_analysis.db import get_engine, init_db
from flood_analysis.study_areas import upsert_study_area_from_tiger_county


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest a Census county-equivalent boundary as a study area.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    parser.add_argument("--geoid", default="51710", help="Census county GEOID. Default: Norfolk city, VA.")
    parser.add_argument("--name", default="Norfolk, VA")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    upsert_study_area_from_tiger_county(engine, args.study_area_id, args.geoid, args.name)
    print(f"Ingested study area {args.study_area_id} from Census GEOID {args.geoid}.")


if __name__ == "__main__":
    main()
