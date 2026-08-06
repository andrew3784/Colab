from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from flood_analysis.scenario_sets import add_scenario_metadata


SQFT_PER_M2 = 10.76391041671
REGIONAL_STUDY_AREA_IDS = [
    "norfolk_va",
    "virginia_beach_va",
    "chesapeake_va",
    "hampton_va",
    "newport_news_va",
    "portsmouth_va",
    "suffolk_va",
]


def calculate_building_damage_estimates(
    engine: Engine,
    study_area_id: str,
    replacement_cost_per_sqft: float = 175.0,
) -> list[dict]:
    """Estimate screening-level building damage from connected building flood impacts."""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM results.connected_building_damage_estimates WHERE study_area_id = :study_area_id"),
            {"study_area_id": study_area_id},
        )
        conn.execute(
            text("DELETE FROM results.connected_building_damage_summary WHERE study_area_id = :study_area_id"),
            {"study_area_id": study_area_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO results.connected_building_damage_estimates (
                    scenario_id, study_area_id, building_id, name, building_type,
                    max_depth_ft, flooded_area_fraction, footprint_area_m2,
                    replacement_cost_per_sqft, estimated_structure_value,
                    damage_class, estimated_damage_fraction, estimated_damage_cost,
                    recovery_class, estimated_recovery_days
                )
                SELECT
                    scenario_id,
                    study_area_id,
                    building_id,
                    name,
                    building_type,
                    max_depth_ft,
                    flooded_area_fraction,
                    footprint_area_m2,
                    :replacement_cost_per_sqft,
                    footprint_area_m2 * :sqft_per_m2 * :replacement_cost_per_sqft,
                    CASE
                        WHEN max_depth_ft < 1 THEN 'minor'
                        WHEN max_depth_ft < 3 THEN 'moderate'
                        WHEN max_depth_ft < 6 THEN 'major'
                        ELSE 'severe'
                    END,
                    CASE
                        WHEN max_depth_ft < 1 THEN 0.05
                        WHEN max_depth_ft < 3 THEN 0.20
                        WHEN max_depth_ft < 6 THEN 0.40
                        ELSE 0.60
                    END,
                    footprint_area_m2 * :sqft_per_m2 * :replacement_cost_per_sqft * flooded_area_fraction *
                    CASE
                        WHEN max_depth_ft < 1 THEN 0.05
                        WHEN max_depth_ft < 3 THEN 0.20
                        WHEN max_depth_ft < 6 THEN 0.40
                        ELSE 0.60
                    END,
                    CASE
                        WHEN max_depth_ft < 1 THEN 'days'
                        WHEN max_depth_ft < 3 THEN 'weeks'
                        WHEN max_depth_ft < 6 THEN 'months'
                        ELSE 'extended'
                    END,
                    CASE
                        WHEN max_depth_ft < 1 THEN 7
                        WHEN max_depth_ft < 3 THEN 30
                        WHEN max_depth_ft < 6 THEN 90
                        ELSE 180
                    END
                FROM results.connected_building_flood_impacts
                WHERE study_area_id = :study_area_id
                  AND max_depth_ft IS NOT NULL
                """
            ),
            {
                "study_area_id": study_area_id,
                "replacement_cost_per_sqft": replacement_cost_per_sqft,
                "sqft_per_m2": SQFT_PER_M2,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO results.connected_building_damage_summary (
                    scenario_id, study_area_id, damaged_building_count,
                    estimated_damage_cost, average_damage_cost,
                    max_estimated_recovery_days
                )
                SELECT
                    scenario_id,
                    study_area_id,
                    count(*),
                    coalesce(sum(estimated_damage_cost), 0),
                    coalesce(avg(estimated_damage_cost), 0),
                    coalesce(max(estimated_recovery_days), 0)
                FROM results.connected_building_damage_estimates
                WHERE study_area_id = :study_area_id
                GROUP BY scenario_id, study_area_id
                """
            ),
            {"study_area_id": study_area_id},
        )
    return building_damage_summary(engine, study_area_id)


def building_damage_summary(engine: Engine, study_area_id: str) -> list[dict]:
    query = text(
        """
        SELECT scenario_id, study_area_id, damaged_building_count,
               estimated_damage_cost, average_damage_cost, max_estimated_recovery_days
        FROM results.connected_building_damage_summary
        WHERE study_area_id = :study_area_id
        ORDER BY estimated_damage_cost, scenario_id
        """
    )
    with engine.connect() as conn:
        return list(conn.execute(query, {"study_area_id": study_area_id}).mappings())


def export_regional_comparison(engine: Engine, output_path: Path, study_area_ids: list[str] | None = None) -> pd.DataFrame:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    study_area_ids = study_area_ids or REGIONAL_STUDY_AREA_IDS
    query = text(
        """
        SELECT
            sa.study_area_id,
            sa.name AS study_area_name,
            s.scenario_id,
            s.sea_level_rise_ft,
            coalesce(bs.building_count, 0) AS building_count,
            coalesce(bs.flooded_building_count, 0) AS flooded_building_count,
            CASE
                WHEN coalesce(bs.building_count, 0) = 0 THEN 0
                ELSE bs.flooded_building_count::double precision / bs.building_count
            END AS flooded_building_fraction,
            coalesce(rs.road_count, 0) AS road_count,
            coalesce(rs.flooded_road_count, 0) AS flooded_road_count,
            coalesce(rs.flooded_length_mi, 0) AS flooded_road_miles,
            coalesce(ds.damaged_building_count, 0) AS damaged_building_count,
            coalesce(ds.estimated_damage_cost, 0) AS estimated_damage_cost,
            coalesce(ds.average_damage_cost, 0) AS average_damage_cost,
            coalesce(ds.max_estimated_recovery_days, 0) AS max_estimated_recovery_days,
            coalesce(ps.acs_year, av.year) AS property_value_acs_year,
            coalesce(ps.median_home_value, av.median_home_value) AS median_home_value,
            coalesce(ps.estimated_exposed_property_value, 0) AS estimated_exposed_property_value
        FROM processed.study_areas sa
        CROSS JOIN processed.flood_scenarios s
        LEFT JOIN results.connected_building_exposure_summary bs
          ON bs.study_area_id = sa.study_area_id
         AND bs.scenario_id = s.scenario_id
        LEFT JOIN results.connected_road_exposure_summary rs
          ON rs.study_area_id = sa.study_area_id
         AND rs.scenario_id = s.scenario_id
        LEFT JOIN results.connected_building_damage_summary ds
          ON ds.study_area_id = sa.study_area_id
         AND ds.scenario_id = s.scenario_id
        LEFT JOIN results.property_value_exposure_summary ps
          ON ps.study_area_id = sa.study_area_id
         AND ps.scenario_id = s.scenario_id
        LEFT JOIN LATERAL (
            SELECT year, median_home_value
            FROM processed.acs_median_home_values
            WHERE study_area_id = sa.study_area_id
              AND median_home_value IS NOT NULL
            ORDER BY year DESC
            LIMIT 1
        ) av ON true
        WHERE (bs.scenario_id IS NOT NULL
            OR rs.scenario_id IS NOT NULL
            OR ds.scenario_id IS NOT NULL
            OR ps.scenario_id IS NOT NULL)
          AND sa.study_area_id = ANY(:study_area_ids)
        ORDER BY sa.name, s.sea_level_rise_ft, s.scenario_id
        """
    )
    frame = add_scenario_metadata(pd.read_sql(query, engine, params={"study_area_ids": study_area_ids}))
    frame.to_csv(output_path, index=False)
    return frame


def export_top_impacted_roads(engine: Engine, output_path: Path, limit_per_group: int = 10) -> pd.DataFrame:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    query = text(
        """
        WITH ranked AS (
            SELECT
                sa.study_area_id,
                sa.name AS study_area_name,
                s.scenario_id,
                s.sea_level_rise_ft,
                i.road_id,
                coalesce(nullif(i.fullname, ''), '(unnamed road)') AS road_name,
                i.mtfcc,
                i.flooded_length_mi,
                i.max_depth_ft,
                row_number() OVER (
                    PARTITION BY sa.study_area_id, s.scenario_id
                    ORDER BY i.flooded_length_mi DESC, i.road_id
                ) AS rank
            FROM results.connected_road_flood_impacts i
            JOIN processed.study_areas sa
              ON sa.study_area_id = i.study_area_id
            JOIN processed.flood_scenarios s
              ON s.scenario_id = i.scenario_id
            WHERE i.study_area_id = ANY(:study_area_ids)
        )
        SELECT study_area_id, study_area_name, scenario_id, sea_level_rise_ft,
               rank, road_id, road_name, mtfcc, flooded_length_mi, max_depth_ft
        FROM ranked
        WHERE rank <= :limit_per_group
        ORDER BY study_area_name, sea_level_rise_ft, rank
        """
    )
    frame = add_scenario_metadata(
        pd.read_sql(
            query,
            engine,
            params={"study_area_ids": REGIONAL_STUDY_AREA_IDS, "limit_per_group": limit_per_group},
        )
    )
    frame.to_csv(output_path, index=False)
    return frame


def export_regional_chart_tables(comparison: pd.DataFrame, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "damage_by_city_scenario": output_dir / "regional_chart_damage_by_city_scenario.csv",
        "flooded_buildings_by_city_scenario": output_dir / "regional_chart_flooded_buildings_by_city_scenario.csv",
        "flooded_road_miles_by_city_scenario": output_dir / "regional_chart_flooded_road_miles_by_city_scenario.csv",
        "exposed_property_value_by_city_scenario": output_dir / "regional_chart_exposed_property_value_by_city_scenario.csv",
        "all_slr_summary": output_dir / "regional_chart_all_slr_summary.csv",
        "decade_summary": output_dir / "regional_chart_decade_summary.csv",
        "year_2100_summary": output_dir / "regional_chart_2100_summary.csv",
        "plus_3ft_summary": output_dir / "regional_chart_plus_3ft_summary.csv",
        "plus_6ft_summary": output_dir / "regional_chart_plus_6ft_summary.csv",
        "scenario_lookup": output_dir / "regional_scenario_lookup.csv",
    }

    damage = comparison.pivot_table(
        index="study_area_name",
        columns="sea_level_rise_ft",
        values="estimated_damage_cost",
        aggfunc="sum",
    ).reset_index()
    buildings = comparison.pivot_table(
        index="study_area_name",
        columns="sea_level_rise_ft",
        values="flooded_building_count",
        aggfunc="sum",
    ).reset_index()
    roads = comparison.pivot_table(
        index="study_area_name",
        columns="sea_level_rise_ft",
        values="flooded_road_miles",
        aggfunc="sum",
    ).reset_index()
    property_value = comparison.pivot_table(
        index="study_area_name",
        columns="sea_level_rise_ft",
        values="estimated_exposed_property_value",
        aggfunc="sum",
    ).reset_index()
    all_slr = comparison.sort_values(["sea_level_rise_ft", "study_area_name"]).copy()
    decade = all_slr[all_slr["scenario_type"] == "future_decade"].copy()
    year_2100 = all_slr[all_slr["planning_year"] == 2100].copy()
    plus_3ft = comparison[comparison["sea_level_rise_ft"] == 3.0].copy()
    plus_3ft = plus_3ft.sort_values("estimated_damage_cost", ascending=False)
    plus_6ft = comparison[comparison["sea_level_rise_ft"] == 6.0].copy()
    plus_6ft = plus_6ft.sort_values("estimated_damage_cost", ascending=False)
    scenario_lookup = comparison[
        ["sea_level_rise_ft", "planning_year", "scenario_type", "scenario_label"]
    ].drop_duplicates().sort_values("sea_level_rise_ft")

    damage.to_csv(paths["damage_by_city_scenario"], index=False)
    buildings.to_csv(paths["flooded_buildings_by_city_scenario"], index=False)
    roads.to_csv(paths["flooded_road_miles_by_city_scenario"], index=False)
    property_value.to_csv(paths["exposed_property_value_by_city_scenario"], index=False)
    all_slr.to_csv(paths["all_slr_summary"], index=False)
    decade.to_csv(paths["decade_summary"], index=False)
    year_2100.to_csv(paths["year_2100_summary"], index=False)
    plus_3ft.to_csv(paths["plus_3ft_summary"], index=False)
    plus_6ft.to_csv(paths["plus_6ft_summary"], index=False)
    scenario_lookup.to_csv(paths["scenario_lookup"], index=False)
    return paths


def export_regional_metric_summaries(comparison: pd.DataFrame, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "metric_summary": output_dir / "regional_metric_summary.csv",
        "plus_3ft_metric_summary": output_dir / "regional_plus_3ft_metric_summary.csv",
        "decade_metric_summary": output_dir / "regional_decade_metric_summary.csv",
        "year_2100_metric_summary": output_dir / "regional_2100_metric_summary.csv",
        "plus_6ft_metric_summary": output_dir / "regional_plus_6ft_metric_summary.csv",
    }
    metric_columns = [
        "flooded_building_count",
        "flooded_building_fraction",
        "flooded_road_count",
        "flooded_road_miles",
        "estimated_damage_cost",
        "estimated_exposed_property_value",
    ]
    aggregations = {"study_area_id": "nunique"}
    aggregations.update(
        {
            column: ["sum", "mean", "median", "min", "max"]
            for column in metric_columns
            if column in comparison.columns
        }
    )
    summary = comparison.groupby("sea_level_rise_ft").agg(aggregations)
    summary.columns = [
        "city_count" if column == ("study_area_id", "nunique") else f"{column[1]}_{column[0]}"
        for column in summary.columns
    ]
    summary = summary.reset_index()
    summary = add_scenario_metadata(summary)
    plus_3ft = summary[summary["sea_level_rise_ft"] == 3.0].copy()
    decade = summary[summary["scenario_type"] == "future_decade"].copy()
    year_2100 = summary[summary["planning_year"] == 2100].copy()
    plus_6ft = summary[summary["sea_level_rise_ft"] == 6.0].copy()

    summary.to_csv(paths["metric_summary"], index=False)
    plus_3ft.to_csv(paths["plus_3ft_metric_summary"], index=False)
    decade.to_csv(paths["decade_metric_summary"], index=False)
    year_2100.to_csv(paths["year_2100_metric_summary"], index=False)
    plus_6ft.to_csv(paths["plus_6ft_metric_summary"], index=False)
    return paths
