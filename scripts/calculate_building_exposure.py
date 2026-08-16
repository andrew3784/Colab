from __future__ import annotations

import argparse

from flood_analysis.db import get_engine, init_db
from flood_analysis.exposure import calculate_connected_building_flood_impacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calculate flooded building footprints by connected flood scenario.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    rows = calculate_connected_building_flood_impacts(engine, args.study_area_id)
    for row in rows:
        print(
            f"scenario_id={row['scenario_id']} building_count={row['building_count']} "
            f"flooded_building_count={row['flooded_building_count']} "
            f"flooded_footprint_area_m2={row['flooded_footprint_area_m2']:.1f}"
        )


if __name__ == "__main__":
    main()
