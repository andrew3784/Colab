from __future__ import annotations

import argparse

from flood_analysis.db import get_engine, init_db
from flood_analysis.scenarios import get_peak_event, upsert_peak_scenarios


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create flood scenarios from peak ingested NOAA water levels.")
    parser.add_argument("--station", default="8638610", help="NOAA station ID. Default: Sewells Point.")
    parser.add_argument("--event-start", required=True, help="Inclusive event start timestamp, e.g. 2026-06-27T00:00:00Z.")
    parser.add_argument("--event-end", required=True, help="Inclusive event end timestamp, e.g. 2026-06-28T23:59:59Z.")
    parser.add_argument("--datum", default="MLLW")
    parser.add_argument("--units", default="english")
    parser.add_argument("--study-area", default="Norfolk pilot")
    parser.add_argument("--sea-level-rise-ft", nargs="+", type=float, default=[0.0, 1.0, 2.0, 3.0])
    parser.add_argument(
        "--method",
        default="bathtub screening; vertical datum conversion pending",
        help="Short method note stored with each scenario.",
    )
    parser.add_argument("--notes", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    peak = get_peak_event(
        engine=engine,
        station_id=args.station,
        event_start=args.event_start,
        event_end=args.event_end,
        datum=args.datum,
        units=args.units,
    )
    scenario_ids = upsert_peak_scenarios(
        engine=engine,
        peak=peak,
        study_area=args.study_area,
        sea_level_rise_values=args.sea_level_rise_ft,
        method=args.method,
        notes=args.notes,
    )
    print(f"Peak water level: {peak.peak_water_level:g} {peak.units} {peak.datum} at {peak.peak_observed_at}")
    for scenario_id in scenario_ids:
        print(f"created_or_updated={scenario_id}")


if __name__ == "__main__":
    main()
