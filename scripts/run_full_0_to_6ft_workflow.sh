#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-./.venv/bin/python}"
ACS_YEAR="${ACS_YEAR:-2023}"
TOP_ROAD_LIMIT="${TOP_ROAD_LIMIT:-10}"
LOG_DIR="${LOG_DIR:-data/processed/logs}"
REPORT_DIR="${REPORT_DIR:-data/processed/gis}"
REPLACEMENT_COST_PER_SQFT="${REPLACEMENT_COST_PER_SQFT:-175}"

mkdir -p "$LOG_DIR" "$REPORT_DIR"
LOG_FILE="$LOG_DIR/full_0_to_6ft_workflow_$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG_FILE") 2>&1

SLR_VALUES=(0 0.5 1 1.5 2 2.5 3 3.75 4 4.5 5 6)
NORFOLK_SCENARIOS=(
  norfolk_pilot_8638610_20260627_plus_0p0ft
  norfolk_pilot_8638610_20260627_plus_0p5ft
  norfolk_pilot_8638610_20260627_plus_1p0ft
  norfolk_pilot_8638610_20260627_plus_1p5ft
  norfolk_pilot_8638610_20260627_plus_2p0ft
  norfolk_pilot_8638610_20260627_plus_2p5ft
  norfolk_pilot_8638610_20260627_plus_3p0ft
  norfolk_pilot_8638610_20260627_plus_3p75ft
  norfolk_pilot_8638610_20260627_plus_4p0ft
  norfolk_pilot_8638610_20260627_plus_4p5ft
  norfolk_pilot_8638610_20260627_plus_5p0ft
  norfolk_pilot_8638610_20260627_plus_6p0ft
)
REGIONAL_CITIES=(
  virginia_beach_va
  chesapeake_va
  hampton_va
  newport_news_va
  portsmouth_va
  suffolk_va
)
GIS_EXPORTS=(
  "norfolk_va=$REPORT_DIR/norfolk_flood_exposure_1m.gpkg"
  "virginia_beach_va=$REPORT_DIR/virginia_beach_flood_exposure_1m.gpkg"
  "chesapeake_va=$REPORT_DIR/chesapeake_flood_exposure_1m.gpkg"
  "hampton_va=$REPORT_DIR/hampton_flood_exposure_1m.gpkg"
  "newport_news_va=$REPORT_DIR/newport_news_flood_exposure_1m.gpkg"
  "portsmouth_va=$REPORT_DIR/portsmouth_flood_exposure_1m.gpkg"
  "suffolk_va=$REPORT_DIR/suffolk_flood_exposure_1m.gpkg"
)

run() {
  printf '\n$'
  printf ' %q' "$@"
  printf '\n'
  "$@"
}

printf 'log_file=%s\n' "$LOG_FILE"
printf 'slr_values_ft=%s\n' "${SLR_VALUES[*]}"

run "$PYTHON_BIN" scripts/create_peak_scenarios.py \
  --event-start 2026-06-27T00:00:00Z \
  --event-end 2026-06-28T23:59:59Z \
  --study-area "Norfolk pilot" \
  --sea-level-rise-ft "${SLR_VALUES[@]}" \
  --method "1-meter Norfolk screening; connected flood extents; 0-6 ft SLR views" \
  --notes "Flood views through +6 ft, including decade SLR planning curve through 2100."

run "$PYTHON_BIN" scripts/convert_scenarios_datum.py \
  --station 8638610 \
  --source-datum MLLW \
  --target-datum NAVD88

for scenario_id in "${NORFOLK_SCENARIOS[@]}"; do
  printf '\n--- Norfolk 1m scenario: %s ---\n' "$scenario_id"
  run "$PYTHON_BIN" scripts/create_flood_depth_rasters.py \
    --study-area-id norfolk_va \
    --dem data/processed/dem/norfolk_va_usgs_1m_hamptonroads_b23_navd88_m.tif \
    --dem-units meters \
    --output-dir data/processed/flood_depths_1m \
    --scenario-id "$scenario_id"
  run "$PYTHON_BIN" scripts/polygonize_flood_extents.py \
    --min-depth-ft 0 \
    --target-resolution 10 \
    --scenario-id "$scenario_id"
  run "$PYTHON_BIN" scripts/create_connected_flood_depth_rasters.py \
    --output-dir data/processed/flood_depths_connected_1m \
    --min-depth-ft 0 \
    --scenario-id "$scenario_id"
  run "$PYTHON_BIN" scripts/polygonize_connected_flood_extents.py \
    --min-depth-ft 0 \
    --target-resolution 10 \
    --scenario-id "$scenario_id"
done

run "$PYTHON_BIN" scripts/calculate_road_exposure.py \
  --study-area-id norfolk_va \
  --connected
run "$PYTHON_BIN" scripts/calculate_building_exposure.py \
  --study-area-id norfolk_va
run "$PYTHON_BIN" scripts/calculate_building_damage.py \
  --study-area-id norfolk_va \
  --replacement-cost-per-sqft "$REPLACEMENT_COST_PER_SQFT"

for study_area_id in "${REGIONAL_CITIES[@]}"; do
  printf '\n--- Regional 1m city: %s ---\n' "$study_area_id"
  run "$PYTHON_BIN" scripts/run_regional_coarse_workflow.py \
    --study-area-id "$study_area_id" \
    --dem-resolution 1m \
    --download-workers 4 \
    --sea-level-rise-ft "${SLR_VALUES[@]}"
done

if ! run "$PYTHON_BIN" scripts/calculate_property_value_exposure.py \
  --year "$ACS_YEAR"; then
  printf '\nWARNING: property value exposure refresh failed; continuing with existing property value rows.\n'
fi
run "$PYTHON_BIN" scripts/export_presentation_outputs.py \
  --output-dir "$REPORT_DIR" \
  --top-road-limit "$TOP_ROAD_LIMIT"

GIS_EXPORT_ARGS=()
for export_spec in "${GIS_EXPORTS[@]}"; do
  GIS_EXPORT_ARGS+=(--export "$export_spec")
done
run "$PYTHON_BIN" scripts/export_gis_layers.py "${GIS_EXPORT_ARGS[@]}"

printf '\nFull 0-6 ft workflow complete.\n'
printf 'log_file=%s\n' "$LOG_FILE"
