from __future__ import annotations

import argparse

from flood_analysis.db import get_engine, init_db
from flood_analysis.exposure import upsert_roads
from flood_analysis.study_areas import upsert_study_area_from_tiger_county


HAMPTON_ROADS_STUDY_AREAS = [
    {"study_area_id": "norfolk_va", "geoid": "51710", "name": "Norfolk, VA"},
    {"study_area_id": "virginia_beach_va", "geoid": "51810", "name": "Virginia Beach, VA"},
    {"study_area_id": "chesapeake_va", "geoid": "51550", "name": "Chesapeake, VA"},
    {"study_area_id": "portsmouth_va", "geoid": "51740", "name": "Portsmouth, VA"},
    {"study_area_id": "hampton_va", "geoid": "51650", "name": "Hampton, VA"},
    {"study_area_id": "newport_news_va", "geoid": "51700", "name": "Newport News, VA"},
    {"study_area_id": "suffolk_va", "geoid": "51800", "name": "Suffolk, VA"},
    {"study_area_id": "poquoson_va", "geoid": "51735", "name": "Poquoson, VA"},
    {"study_area_id": "york_county_va", "geoid": "51199", "name": "York County, VA"},
    {"study_area_id": "james_city_county_va", "geoid": "51095", "name": "James City County, VA"},
    {"study_area_id": "isle_of_wight_county_va", "geoid": "51093", "name": "Isle of Wight County, VA"},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest Hampton Roads Census county-equivalent study areas.")
    parser.add_argument(
        "--only",
        nargs="+",
        choices=[area["study_area_id"] for area in HAMPTON_ROADS_STUDY_AREAS],
        help="Optional subset of study_area_id values to ingest.",
    )
    parser.add_argument(
        "--include-roads",
        action="store_true",
        help="Also ingest TIGER roads clipped to each selected study area.",
    )
    return parser.parse_args()


def selected_study_areas(only: list[str] | None) -> list[dict[str, str]]:
    if not only:
        return HAMPTON_ROADS_STUDY_AREAS
    selected = set(only)
    return [area for area in HAMPTON_ROADS_STUDY_AREAS if area["study_area_id"] in selected]


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    for area in selected_study_areas(args.only):
        upsert_study_area_from_tiger_county(engine, area["study_area_id"], area["geoid"], area["name"])
        print(f"ingested_study_area={area['study_area_id']} geoid={area['geoid']}")
        if args.include_roads:
            road_count = upsert_roads(engine, area["study_area_id"], area["geoid"])
            print(f"ingested_roads={road_count} study_area_id={area['study_area_id']}")


if __name__ == "__main__":
    main()
