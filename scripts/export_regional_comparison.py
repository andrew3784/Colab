from __future__ import annotations

import argparse
from pathlib import Path

from flood_analysis.db import get_engine, init_db
from flood_analysis.impacts import export_regional_comparison


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export cross-study-area flood exposure and damage comparison CSV.")
    parser.add_argument("--output", type=Path, default=Path("data/processed/gis/regional_flood_comparison.csv"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    frame = export_regional_comparison(engine, args.output)
    print(f"exported={args.output} rows={len(frame)}")


if __name__ == "__main__":
    main()
