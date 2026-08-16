from __future__ import annotations

import argparse

from flood_analysis.datums import convert_scenarios_to_datum
from flood_analysis.db import get_engine, init_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert scenario water surfaces between NOAA station datums.")
    parser.add_argument("--station", default="8638610", help="NOAA station ID. Default: Sewells Point.")
    parser.add_argument("--source-datum", default="MLLW")
    parser.add_argument("--target-datum", default="NAVD88")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    count = convert_scenarios_to_datum(engine, args.station, args.source_datum, args.target_datum)
    print(f"Converted {count} scenarios from {args.source_datum} to {args.target_datum} for station {args.station}.")


if __name__ == "__main__":
    main()
