from __future__ import annotations

import argparse

from flood_analysis.db import get_engine, init_db
from flood_analysis.property_values import calculate_property_value_exposure, fetch_acs_median_home_values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estimate residential property-value exposure using ACS median home values.")
    parser.add_argument("--year", type=int, default=2022)
    parser.add_argument("--study-area-id", action="append", help="Optional study area subset. Repeat for multiple.")
    parser.add_argument("--skip-fetch", action="store_true", help="Use already-ingested ACS values.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    if not args.skip_fetch:
        rows = fetch_acs_median_home_values(engine, args.year, args.study_area_id)
        for row in rows:
            value = row["median_home_value"]
            print(f"acs_value study_area_id={row['study_area_id']} year={row['year']} median_home_value={value}")
    rows = calculate_property_value_exposure(engine, args.year, args.study_area_id)
    for row in rows:
        print(
            f"scenario_id={row['scenario_id']} study_area_id={row['study_area_id']} "
            f"estimated_exposed_property_value=${row['estimated_exposed_property_value']:,.0f}"
        )


if __name__ == "__main__":
    main()
