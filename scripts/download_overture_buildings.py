from __future__ import annotations

import argparse
import re
from pathlib import Path

import duckdb

from flood_analysis.db import get_engine
from flood_analysis.rasters import read_study_area


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Overture building candidates for a study-area bounding box.")
    parser.add_argument("--study-area-id", default="norfolk_va")
    parser.add_argument("--release", default="2026-06-17.0")
    parser.add_argument("--output", type=Path, default=Path("data/raw/overture/norfolk_buildings.parquet"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", args.release):
        raise RuntimeError("Release must use the Overture YYYY-MM-DD.N format")
    west, south, east, north = read_study_area(get_engine(), args.study_area_id).total_bounds
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.unlink(missing_ok=True)
    source = (
        "s3://overturemaps-us-west-2/release/"
        f"{args.release}/theme=buildings/type=building/*"
    )
    connection = duckdb.connect()
    connection.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    connection.execute("SET s3_region='us-west-2'")
    output = str(args.output).replace("'", "''")
    connection.execute(
        f"""
        COPY (
            SELECT
                id,
                names.primary AS name,
                subtype AS building_type,
                geometry
            FROM read_parquet('{source}', filename=true, hive_partitioning=1)
            WHERE bbox.xmin < {east}
              AND bbox.ymin < {north}
              AND bbox.xmax > {west}
              AND bbox.ymax > {south}
        ) TO '{output}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    count = connection.execute("SELECT count(*) FROM read_parquet(?)", [str(args.output)]).fetchone()[0]
    print(f"downloaded={args.output} candidate_buildings={count}")


if __name__ == "__main__":
    main()
