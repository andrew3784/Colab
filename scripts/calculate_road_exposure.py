from __future__ import annotations

import argparse

from flood_analysis.db import get_engine, init_db
from flood_analysis.exposure import calculate_connected_road_flood_impacts, calculate_road_flood_impacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calculate flooded road length by scenario.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    parser.add_argument("--connected", action="store_true", help="Use connected flood extents instead of unfiltered bathtub extents.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    rows = calculate_connected_road_flood_impacts(engine, args.study_area_id) if args.connected else calculate_road_flood_impacts(engine, args.study_area_id)
    for row in rows:
        print(
            f"scenario_id={row['scenario_id']} road_count={row['road_count']} "
            f"flooded_road_count={row['flooded_road_count']} flooded_length_mi={row['flooded_length_mi']:.3f}"
        )


if __name__ == "__main__":
    main()
