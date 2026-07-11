from __future__ import annotations

import argparse

from flood_analysis.datums import upsert_station_datums
from flood_analysis.db import get_engine, init_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest NOAA station datum offsets into PostGIS.")
    parser.add_argument("--station", default="8638610", help="NOAA station ID. Default: Sewells Point.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    count = upsert_station_datums(engine, args.station)
    print(f"Ingested {count} NOAA datum offsets for station {args.station}.")


if __name__ == "__main__":
    main()
