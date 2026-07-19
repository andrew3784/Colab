from __future__ import annotations

import argparse
from pathlib import Path

from flood_analysis.db import get_engine, init_db
from flood_analysis.impacts import (
    export_regional_chart_tables,
    export_regional_comparison,
    export_regional_metric_summaries,
    export_top_impacted_roads,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export presentation-ready regional flood impact tables.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/gis"))
    parser.add_argument("--top-road-limit", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    comparison_path = args.output_dir / "regional_flood_comparison.csv"
    top_roads_path = args.output_dir / "regional_top_impacted_roads.csv"
    comparison = export_regional_comparison(engine, comparison_path)
    top_roads = export_top_impacted_roads(engine, top_roads_path, args.top_road_limit)
    chart_paths = export_regional_chart_tables(comparison, args.output_dir)
    metric_paths = export_regional_metric_summaries(comparison, args.output_dir)
    print(f"exported={comparison_path} rows={len(comparison)}")
    print(f"exported={top_roads_path} rows={len(top_roads)}")
    for path in chart_paths.values():
        print(f"exported={path}")
    for path in metric_paths.values():
        print(f"exported={path}")


if __name__ == "__main__":
    main()
