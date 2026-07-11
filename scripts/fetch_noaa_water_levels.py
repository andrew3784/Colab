from __future__ import annotations

import argparse
from pathlib import Path

from flood_analysis.noaa import NoaaRequest, fetch_water_levels


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch NOAA CO-OPS water levels to a CSV file.")
    parser.add_argument("--station", required=True, help="NOAA station ID, e.g. 8638610 for Sewells Point.")
    parser.add_argument("--begin-date", required=True, help="YYYYMMDD")
    parser.add_argument("--end-date", required=True, help="YYYYMMDD")
    parser.add_argument("--datum", default="MLLW")
    parser.add_argument("--units", default="english", choices=["english", "metric"])
    parser.add_argument("--interval", default="6", help="NOAA interval, commonly 6 or h.")
    parser.add_argument("--output", type=Path, help="CSV output path. Defaults under data/raw/noaa/.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    request = NoaaRequest(
        station=args.station,
        begin_date=args.begin_date,
        end_date=args.end_date,
        datum=args.datum,
        units=args.units,
        interval=args.interval,
    )
    water_levels = fetch_water_levels(request)
    output = args.output or Path(
        f"data/raw/noaa/water_levels_{args.station}_{args.begin_date}_{args.end_date}_{args.datum}_{args.units}.csv"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    water_levels.to_csv(output, index=False)
    print(f"Wrote {len(water_levels)} NOAA water-level observations to {output}.")


if __name__ == "__main__":
    main()
