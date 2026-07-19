from __future__ import annotations

import argparse

from flood_analysis.db import get_engine, init_db
from flood_analysis.impacts import calculate_building_damage_estimates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estimate screening-level building flood damage by scenario.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    parser.add_argument(
        "--replacement-cost-per-sqft",
        type=float,
        default=175.0,
        help="Assumed structure replacement cost used for screening estimates. Default: 175 dollars/sq ft.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    rows = calculate_building_damage_estimates(engine, args.study_area_id, args.replacement_cost_per_sqft)
    for row in rows:
        print(
            f"scenario_id={row['scenario_id']} damaged_building_count={row['damaged_building_count']} "
            f"estimated_damage_cost=${row['estimated_damage_cost']:,.0f} "
            f"max_estimated_recovery_days={row['max_estimated_recovery_days']}"
        )


if __name__ == "__main__":
    main()
