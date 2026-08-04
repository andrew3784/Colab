from __future__ import annotations

import argparse
from pathlib import Path

from flood_analysis.db import get_engine, init_db
from flood_analysis.exposure import export_road_impacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export GIS-ready project layers to a GeoPackage.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    parser.add_argument("--output", type=Path, default=Path("data/processed/gis/norfolk_flood_road_exposure.gpkg"))
    parser.add_argument(
        "--export",
        action="append",
        metavar="STUDY_AREA_ID=OUTPUT.gpkg",
        help="Export one study area to a GeoPackage. May be repeated; overrides --study-area-id/--output.",
    )
    return parser.parse_args()


def parse_export_specs(args: argparse.Namespace) -> list[tuple[str, Path]]:
    if not args.export:
        return [(args.study_area_id, args.output)]

    exports: list[tuple[str, Path]] = []
    for spec in args.export:
        if "=" not in spec:
            raise SystemExit(f"invalid --export {spec!r}; expected STUDY_AREA_ID=OUTPUT.gpkg")
        study_area_id, output = spec.split("=", 1)
        if not study_area_id or not output:
            raise SystemExit(f"invalid --export {spec!r}; expected STUDY_AREA_ID=OUTPUT.gpkg")
        exports.append((study_area_id, Path(output)))
    return exports


def main() -> None:
    args = parse_args()
    engine = get_engine()
    init_db(engine)
    for study_area_id, output in parse_export_specs(args):
        export_road_impacts(engine, study_area_id, output)
        print(f"exported={output}")


if __name__ == "__main__":
    main()
