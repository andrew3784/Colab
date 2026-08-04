# Hampton Roads Flood Analysis, Old Dominion Univ., Summer 2026

Andrew Mounsey amounsey@odu.edu

Ryan Thompson rthom035@odu.com

Scott Zumwalt jzum001@odu.edu

Python-first workflow for ingesting NOAA water-level data, storing project data in PostgreSQL/PostGIS, creating flood-depth rasters, calculating road/building exposure, and exporting GIS-ready outputs for ArcGIS or QGIS.

## What This Produces

The active Norfolk workflow produces:

- NOAA-derived flood scenarios for Sewells Point station `8638610`.
- A Norfolk study-area boundary in PostGIS.
- A clipped USGS 3DEP 1-meter DEM for Norfolk.
- Four flood-depth GeoTIFFs for current, `+1 ft`, `+2 ft`, and `+3 ft` sea-level-rise scenarios.
- Four connectivity-filtered flood-depth GeoTIFFs and vector flood extents.
- TIGER road exposure tables and summaries.
- Overture building-footprint exposure tables and summaries.
- Screening-level building damage-cost and recovery-time estimates.
- A GeoPackage and summary CSV files for GIS review.

## Before You Start

Run commands from the repository root:

```bash
pwd
```

Expected location:

```text
/home/rthomson/odu/cs620/project
```

You need:

- Python `3.9` or newer.
- PostgreSQL with PostGIS enabled.
- A `.env` file containing `DATABASE_URL`.
- Internet access for NOAA, USGS, TIGER, and Overture downloads.
- Enough local disk space for source DEM tiles, derived rasters, and GIS exports. Budget at least `20-30 GB` for a full rerun.

Large data files are intentionally kept on disk as GeoTIFF, Parquet, GeoPackage, or CSV files. PostGIS stores scenario metadata, vector layers, exposure tables, and raster file paths.

## Setup

Create a virtual environment and install the project:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

Create `.env` in the repository root:

```bash
DATABASE_URL=postgresql+psycopg://user:password@host:5432/database
```

Use the SQLAlchemy/psycopg URL format shown above. Do not commit `.env`.

## Quick Health Check

Verify PostGIS first:

```bash
python scripts/check_postgis.py
```

If that fails, inspect the connected user's privileges:

```bash
python scripts/check_db_privileges.py
```

The database owner may need to run `sql/bootstrap.sql`, enable the PostGIS extension, or grant this user permission to create schemas and tables.

Initialize project schemas and tables:

```bash
python scripts/init_db.py
```

## Full Run Order

Run the sections below in order for a clean end-to-end rebuild. Some steps are safe to rerun because they upsert rows or overwrite/register outputs, but the cleanest mental model is still top-to-bottom execution.

## 1. Ingest NOAA Water Levels

If database writes are not available yet, you can fetch NOAA data to CSV only:

```bash
python scripts/fetch_noaa_water_levels.py \
  --station 8638610 \
  --begin-date 20260627 \
  --end-date 20260628
```

For the normal workflow, write observations and station metadata to PostGIS:

```bash
python scripts/ingest_noaa_water_levels.py \
  --station 8638610 \
  --begin-date 20260627 \
  --end-date 20260628 \
  --datum MLLW \
  --units english \
  --interval 6
```

This writes observations to `raw.noaa_water_levels` and station metadata to `raw.noaa_stations`.

## 2. Create Flood Scenarios

Create scenarios from the peak observed water level. The default sea-level-rise variants are current conditions plus `1`, `2`, and `3` feet.

```bash
python scripts/create_peak_scenarios.py \
  --event-start 2026-06-27T00:00:00Z \
  --event-end 2026-06-28T23:59:59Z \
  --study-area "Norfolk pilot"
```

This writes rows to `processed.flood_scenarios`.

## 3. Convert Scenarios To NAVD88

The NOAA water levels above are ingested in `MLLW`. Convert them before comparing to the DEM.

Ingest station datum offsets:

```bash
python scripts/ingest_noaa_datums.py --station 8638610
```

Convert scenario water surfaces from `MLLW` to `NAVD88`:

```bash
python scripts/convert_scenarios_datum.py \
  --station 8638610 \
  --source-datum MLLW \
  --target-datum NAVD88
```

Use `analysis_water_surface_elevation` and `analysis_datum` for DEM comparisons, not the original `water_surface_elevation` and `datum` fields.

Current Norfolk pilot scenario elevations:

```text
current peak: 1.536 ft NAVD88
+1 ft SLR:    2.536 ft NAVD88
+2 ft SLR:    3.536 ft NAVD88
+3 ft SLR:    4.536 ft NAVD88
```

Optional expanded flood-view scenario set for flooding on top of sea-level rise:

```text
0.00 ft, 0.50 ft, 1.00 ft, 1.50 ft, 2.00 ft, 2.50 ft, 3.00 ft, 3.75 ft, 4.00 ft, 4.50 ft, 5.00 ft, 6.00 ft
```

The decade planning curve for climate-change and sea-level-rise impacts through 2100 is:

```text
2030: +0.50 ft SLR
2040: +1.00 ft SLR
2050: +1.50 ft SLR
2060: +2.00 ft SLR
2070: +2.50 ft SLR
2080: +3.00 ft SLR
2090: +3.75 ft SLR
2100: +4.50 ft SLR
```

The `+4`, `+5`, and `+6 ft` scenarios are additional flood-view stress tests on top of the same event baseline. This keeps the established model: NOAA Sewells Point event peak plus an added sea-level-rise increment, converted to NAVD88 before comparison to the DEM. Treat the decade values as a planning curve for presentation and sensitivity testing. Cite NOAA's 2022 Sea Level Rise Technical Report as the source for scenario-based U.S. sea-level-rise planning; the specific decade values above are a simplified interpolation from the selected planning anchors.

The notebook adds a climate-possibility framing layer on top of these deterministic flood outputs. It labels available scenarios as an observed event benchmark, future sea-level-rise planning possibilities, or stress-test possibilities. This is intentionally not an annual exceedance probability or return-period model; the spatial workflow answers what is exposed if a modeled water surface occurs, not how often that water surface will occur.

## 4. Ingest Norfolk Study Area

Load the Norfolk city boundary from US Census TIGER/Line county-equivalent boundaries:

```bash
python scripts/ingest_study_area.py \
  --study-area-id norfolk_va \
  --geoid 51710 \
  --name "Norfolk, VA"
```

This writes a valid `MultiPolygon` to `processed.study_areas`.

Current Norfolk geometry summary:

```text
249.68 sq km
96.40 sq mi
```

## 5. Download And Clip The 1-Meter DEM

The active exposure workflow uses USGS 3DEP 1-meter products. The recommended product set is `VA_HamptonRoads_B23`, published `2026-03-24`.

Search for matching tiles:

```bash
python scripts/download_usgs_dem.py \
  --study-area-id norfolk_va \
  --dataset "Digital Elevation Model (DEM) 1 meter" \
  --title-contains VA_HamptonRoads_B23 \
  --max-results 100
```

Expected search result:

```text
product_count=9
total_bytes=2361737796
```

Download the tiles with resumable, size-validated transfers:

```bash
python scripts/download_usgs_dem.py \
  --study-area-id norfolk_va \
  --dataset "Digital Elevation Model (DEM) 1 meter" \
  --title-contains VA_HamptonRoads_B23 \
  --max-results 100 \
  --output-dir data/raw/usgs_3dep_1m \
  --workers 4 \
  --download
```

Clip and mosaic the downloaded tiles to Norfolk:

```bash
python scripts/clip_dem_to_study_area.py \
  --study-area-id norfolk_va \
  --input-dem data/raw/usgs_3dep_1m/*.tif \
  --output data/processed/dem/norfolk_va_usgs_1m_hamptonroads_b23_navd88_m.tif
```

Current clipped 1-meter DEM validation:

```text
crs=EPSG:26918
resolution=1x1 meter
shape=24284x19010
valid_pixels=176426700
nodata=-999999.0
min_m=-7.605
max_m=21.083
mean_m=2.443
```

## 6. Create Flood-Depth Rasters

Create one high-resolution flood-depth GeoTIFF per NAVD88 scenario:

```bash
python scripts/create_flood_depth_rasters.py \
  --study-area-id norfolk_va \
  --dem data/processed/dem/norfolk_va_usgs_1m_hamptonroads_b23_navd88_m.tif \
  --dem-units meters \
  --output-dir data/processed/flood_depths_1m
```

The script registers raster paths and summary stats in `results.flood_depth_rasters`.

## 7. Create Connected Flood Products

First polygonize the unfiltered flood-depth rasters. The export step writes this baseline `flood_extents` layer, and the connected products below are compared against it.

```bash
python scripts/polygonize_flood_extents.py --min-depth-ft 0
```

Create connected-depth rasters from the flood-depth rasters:

```bash
python scripts/create_connected_flood_depth_rasters.py \
  --output-dir data/processed/flood_depths_connected_1m \
  --min-depth-ft 0
```

This keeps wet cells connected by 8-neighbor connectivity to the raster edge or clipped study-area/nodata boundary. It removes isolated low depressions that are not connected to the modeled boundary water source.

Polygonize the connected rasters into PostGIS vector extents:

```bash
python scripts/polygonize_connected_flood_extents.py --min-depth-ft 0
```

This writes vector extents to `processed.connected_flood_extents` and preserves the unfiltered bathtub extents in `processed.flood_extents`.

Connectivity caveat: this is a screening-level boundary-connectivity filter, not a hydrodynamic model. It does not simulate flow paths, culverts, barriers, drainage, tide timing, or wave effects.

## 8. Calculate Road Exposure

Ingest Census TIGER roads clipped to Norfolk:

```bash
python scripts/ingest_roads.py \
  --study-area-id norfolk_va \
  --geoid 51710
```

Calculate road exposure against the unfiltered baseline extents:

```bash
python scripts/calculate_road_exposure.py --study-area-id norfolk_va
```

Then calculate road exposure against connectivity-filtered extents:

```bash
python scripts/calculate_road_exposure.py \
  --study-area-id norfolk_va \
  --connected
```

The first command writes unfiltered impacts to `results.road_flood_impacts` and `results.road_exposure_summary`. The second command writes connected impacts to `results.connected_road_flood_impacts` and `results.connected_road_exposure_summary`.

Current connected road exposure summary:

```text
road_count=6663
connected_road_impact_rows=823

current peak: flooded_road_count=73  flooded_length_mi=12.439
+1 ft SLR:    flooded_road_count=113 flooded_length_mi=13.724
+2 ft SLR:    flooded_road_count=215 flooded_length_mi=19.770
+3 ft SLR:    flooded_road_count=422 flooded_length_mi=43.972
```

Road exposure caveats:

- TIGER roads are a generalized public baseline, not a routable transportation network.
- The connected outputs reduce disconnected-depression artifacts but are still screening-level.
- Use VGIN/local centerlines before making transportation decisions.

## 9. Download And Ingest Building Footprints

The current building source is Overture Maps release `2026-06-17.0`. Download Norfolk bounding-box candidates from Overture GeoParquet with DuckDB:

```bash
python scripts/download_overture_buildings.py \
  --study-area-id norfolk_va \
  --output data/raw/overture/norfolk_buildings.parquet
```

The extract contains `141441` candidates. Exact clipping to the Norfolk study area loads `83891` footprints:

```bash
python scripts/ingest_buildings_from_file.py \
  --study-area-id norfolk_va \
  --input data/raw/overture/norfolk_buildings.parquet \
  --id-column id \
  --name-column name \
  --type-column building_type \
  --source "Overture Maps 2026-06-17.0"
```

This writes footprints to `processed.buildings`.

Alternative local footprint files are also supported:

```bash
python scripts/ingest_buildings_from_file.py \
  --study-area-id norfolk_va \
  --input path/to/building_footprints.gpkg \
  --layer buildings \
  --id-column building_id \
  --name-column name \
  --type-column building_type \
  --source "Local building footprints"
```

Experimental OSM ingestion exists, but the full Norfolk OSM/Overpass import is large and timed out in this environment. Prefer Overture or a local file for reliable runs.

## 10. Calculate Building Exposure

After buildings and connected rasters exist, calculate building exposure:

```bash
python scripts/calculate_building_exposure.py --study-area-id norfolk_va
```

This writes feature-level impacts to `results.connected_building_flood_impacts` and scenario summaries to `results.connected_building_exposure_summary`.

Current 1-meter connected building exposure summary:

```text
processed.buildings=83891
connected_building_impact_rows=2650

current peak: flooded_building_count=169  flooded_footprint_area_m2=133363.3
+1 ft SLR:    flooded_building_count=196  flooded_footprint_area_m2=136707.9
+2 ft SLR:    flooded_building_count=512  flooded_footprint_area_m2=166650.7
+3 ft SLR:    flooded_building_count=1773 flooded_footprint_area_m2=307445.4
```

Each reported building has a positive maximum depth sampled from its connected 1-meter raster. Polygon-only sliver contacts without a wet raster pixel are excluded.

## 11. Estimate Building Damage

After connected building exposure exists, estimate screening-level structure damage costs and recovery categories:

```bash
python scripts/calculate_building_damage.py \
  --study-area-id norfolk_va \
  --replacement-cost-per-sqft 175
```

This writes feature-level estimates to `results.connected_building_damage_estimates` and scenario summaries to `results.connected_building_damage_summary`.

The default model is intentionally simple and explainable for project comparison:

```text
max_depth_ft < 1: minor damage,    5% damage, estimated recovery in days
1-3 ft:           moderate damage, 20% damage, estimated recovery in weeks
3-6 ft:           major damage,    40% damage, estimated recovery in months
>6 ft:            severe damage,   60% damage, extended recovery
```

Estimated damage cost is calculated from footprint area, the assumed replacement cost per square foot, flooded footprint fraction, and the depth-based damage percentage. Treat this as a screening-level comparison metric, not an appraisal, insurance estimate, or engineering loss assessment.

## 12. Export GIS Deliverables

Export GIS layers for ArcGIS or QGIS review:

```bash
python scripts/export_gis_layers.py \
  --study-area-id norfolk_va \
  --output data/processed/gis/norfolk_flood_exposure_1m.gpkg
```

The GeoPackage currently contains these layers when all prior steps have run:

```text
roads
flood_extents
road_flood_impacts
connected_flood_extents
connected_road_flood_impacts
buildings
connected_building_flood_impacts
connected_building_damage_estimates
```

The export also writes adjacent road, building, and damage scenario-summary CSV files.

## 13. Export Regional Comparison

After one or more study areas have road, building, or damage summaries, export a cross-area comparison table:

```bash
python scripts/export_regional_comparison.py \
  --output data/processed/gis/regional_flood_comparison.csv
```

The output compares each available study area and scenario using:

- building count
- flooded building count
- flooded building fraction
- road count
- flooded road count
- flooded road miles
- damaged building count
- estimated damage cost
- average damage cost
- maximum estimated recovery days

The regional analysis is intentionally frozen at seven major Hampton Roads cities: Norfolk, Virginia Beach, Chesapeake, Hampton, Newport News, Portsmouth, and Suffolk. This keeps the project scope large enough for comparison while avoiding a data-processing-only project.

Export presentation-ready tables after the regional comparison has been generated:

```bash
python scripts/export_presentation_outputs.py \
  --output-dir data/processed/gis \
  --top-road-limit 10
```

Export the notebook report to HTML without installing TeX/xelatex:

```bash
python scripts/export_notebook_html.py
```

This writes `data/processed/gis/cs620_project_efs_report.html`. Open that file in a browser and use the browser's print dialog to save it as a PDF. Direct Jupyter PDF export requires a TeX installation such as `xelatex`.

This writes:

- `regional_flood_comparison.csv`
- `regional_top_impacted_roads.csv`
- `regional_chart_damage_by_city_scenario.csv`
- `regional_chart_flooded_buildings_by_city_scenario.csv`
- `regional_chart_flooded_road_miles_by_city_scenario.csv`
- `regional_chart_exposed_property_value_by_city_scenario.csv`
- `regional_chart_all_slr_summary.csv`
- `regional_chart_decade_summary.csv`
- `regional_chart_2100_summary.csv`
- `regional_chart_plus_3ft_summary.csv`
- `regional_chart_plus_6ft_summary.csv`
- `regional_metric_summary.csv`
- `regional_plus_3ft_metric_summary.csv`
- `regional_decade_metric_summary.csv`
- `regional_2100_metric_summary.csv`
- `regional_plus_6ft_metric_summary.csv`
- `regional_scenario_lookup.csv`

To add Census ACS median-home-value exposure estimates to the regional outputs:

```bash
python scripts/calculate_property_value_exposure.py --year 2023
python scripts/export_presentation_outputs.py --output-dir data/processed/gis
```

The property-value estimate uses Census Reporter / ACS table `B25077` median owner-occupied home value for each city/county-equivalent. It estimates exposed residential property value as median home value multiplied by flooded building area fraction. This is a uniform 7-city proxy, not parcel-level assessed value.

To run the optional full `0-6 ft` scenario set from the notebook, open `cs620_project_efs.ipynb`, set `RUN_FULL_0_TO_6FT_WORKFLOW = True`, and run the notebook top to bottom. The notebook uses these SLR increments:

```text
0 0.5 1 1.5 2 2.5 3 3.75 4 4.5 5 6
```

The same full workflow is available as a resumable terminal-oriented script that logs progress to `data/processed/logs/`:

```bash
scripts/run_full_0_to_6ft_workflow.sh
```

If running over SSH, prefer `tmux` or `screen` because the 1-meter Norfolk rasters and city GeoPackage exports can run for a long time.

Manual equivalent for the six coarse regional cities:

```bash
python scripts/run_regional_coarse_workflow.py \
  --study-area-id virginia_beach_va \
  --study-area-id chesapeake_va \
  --study-area-id hampton_va \
  --study-area-id newport_news_va \
  --study-area-id portsmouth_va \
  --study-area-id suffolk_va \
  --sea-level-rise-ft 0 0.5 1 1.5 2 2.5 3 3.75 4 4.5 5 6
```

Norfolk uses the higher-resolution 1-meter workflow, so generate its future scenarios with the standard Norfolk raster/exposure sequence:

```bash
python scripts/create_peak_scenarios.py \
  --event-start 2026-06-27T00:00:00Z \
  --event-end 2026-06-28T23:59:59Z \
  --study-area "Norfolk pilot" \
  --sea-level-rise-ft 0 0.5 1 1.5 2 2.5 3 3.75 4 4.5 5 6 \
  --method "1-meter Norfolk screening; connected flood extents; 0-6 ft SLR views"

python scripts/convert_scenarios_datum.py --station 8638610 --source-datum MLLW --target-datum NAVD88

python scripts/create_flood_depth_rasters.py \
  --study-area-id norfolk_va \
  --dem data/processed/dem/norfolk_va_usgs_1m_hamptonroads_b23_navd88_m.tif \
  --dem-units meters \
  --output-dir data/processed/flood_depths_1m

python scripts/polygonize_flood_extents.py --min-depth-ft 0
python scripts/create_connected_flood_depth_rasters.py --output-dir data/processed/flood_depths_connected_1m --min-depth-ft 0
python scripts/polygonize_connected_flood_extents.py --min-depth-ft 0
python scripts/calculate_road_exposure.py --study-area-id norfolk_va --connected
python scripts/calculate_building_exposure.py --study-area-id norfolk_va
python scripts/calculate_building_damage.py --study-area-id norfolk_va --replacement-cost-per-sqft 175
python scripts/calculate_property_value_exposure.py --year 2023
python scripts/export_presentation_outputs.py --output-dir data/processed/gis --top-road-limit 10
```

After the expanded workflow runs, the notebook adds decade summary, `2100 / +4.5 ft`, and `+6 ft` stress-view result tables using the same report CSVs. GIS GeoPackage scenario layers and adjacent summary CSVs include `sea_level_rise_ft`, `planning_year`, `scenario_type`, and `scenario_label` fields for filtering in QGIS or ArcGIS.

Current `+3 ft` regional summary:

```text
City               Buildings  Road miles  Damage   Exposed property value
Norfolk                 1773       43.97  $205.1M                 $355.0M
Virginia Beach           589      152.59   $45.0M                 $227.9M
Chesapeake               148       41.42   $23.0M                  $55.2M
Hampton                  241       60.84   $15.7M                  $61.9M
Newport News              58       33.06   $14.8M                  $15.1M
Portsmouth                20       12.17    $2.0M                   $4.9M
Suffolk                   11       12.74    $1.4M                   $4.0M
```

Current `+3 ft` seven-city aggregate metrics:

```text
Metric                         Total        Mean/city    Median/city   Min/city     Max/city
Flooded buildings              2,840          405.7          148.0        11.0       1,773.0
Flooded road count             2,479          354.1          291.0        99.0         942.0
Flooded road miles            356.79          50.97          41.42       12.17        152.59
Estimated damage            $307.0M         $43.9M         $15.7M       $1.4M       $205.1M
Exposed property value       $723.9M        $103.4M         $55.2M       $4.0M       $355.0M
```

Presentation story:

- Norfolk has the highest estimated building damage and flooded building count in the current outputs.
- Virginia Beach has the highest flooded road mileage under the `+3 ft` scenario.
- Chesapeake, Hampton, and Newport News show meaningful regional exposure even with fewer damaged buildings than Norfolk or Virginia Beach.
- Portsmouth and Suffolk have lower building-damage totals in this screening run, but still show measurable road disruption.

Regional comparison caveats:

- Norfolk uses the high-resolution 1-meter DEM workflow; the other six cities currently use the coarse regional DEM workflow.
- The coarse regional workflow is appropriate for comparison and presentation tables, not parcel-level engineering conclusions.
- The connected flood outputs are static inundation screening products, not a hydrodynamic flood simulation.
- The building-damage estimates use a simple depth-based replacement-cost model and should not be treated as insurance, appraisal, or engineering loss estimates.
- The property-value exposure estimate uses city-level ACS median home values. It is a neighborhood-scale proxy and does not represent parcel assessments, sale prices, commercial property values, or tax appraisals.
- All current regional scenarios use Sewells Point station datum conversion. A production regional model should evaluate spatially varying tidal datums or VDatum.
- Future decade and `+6 ft` stress-view scenarios use the same static connected-inundation model with larger sea-level-rise increments. They account for flooding on top of the selected sea-level-rise increment, but do not model changing storm climatology, rainfall, drainage capacity, shoreline adaptation, subsidence beyond the SLR increment, or future development patterns.

To register Hampton Roads locality boundaries in one command:

```bash
python scripts/ingest_hampton_roads_study_areas.py
```

For the frozen seven-city regional scope, ingest boundaries plus TIGER roads:

```bash
python scripts/ingest_hampton_roads_study_areas.py \
  --only norfolk_va virginia_beach_va chesapeake_va hampton_va newport_news_va portsmouth_va suffolk_va \
  --include-roads
```

If a road ingest is slow or interrupted, rerun one locality at a time:

```bash
python scripts/ingest_roads.py --study-area-id virginia_beach_va --geoid 51810
python scripts/ingest_roads.py --study-area-id chesapeake_va --geoid 51550
python scripts/ingest_roads.py --study-area-id portsmouth_va --geoid 51740
python scripts/ingest_roads.py --study-area-id hampton_va --geoid 51650
python scripts/ingest_roads.py --study-area-id newport_news_va --geoid 51700
python scripts/ingest_roads.py --study-area-id suffolk_va --geoid 51800
```

Study-area and road ingestion only creates the regional inventory. Each locality still needs its own DEM clip, flood rasters, connected flood extents, building footprints, exposure calculation, and damage calculation before it appears in the regional flood-impact comparison.

For a fast end-to-end regional proof of concept, run the coarse workflow for the first batch:

```bash
python scripts/run_regional_coarse_workflow.py \
  --study-area-id portsmouth_va

python scripts/run_regional_coarse_workflow.py \
  --study-area-id chesapeake_va

python scripts/run_regional_coarse_workflow.py \
  --study-area-id virginia_beach_va

python scripts/run_regional_coarse_workflow.py \
  --study-area-id hampton_va

python scripts/run_regional_coarse_workflow.py \
  --study-area-id newport_news_va

python scripts/run_regional_coarse_workflow.py \
  --study-area-id suffolk_va
```

This creates city-specific scenarios, downloads and clips coarse USGS DEM tiles, creates flood-depth rasters, creates connected extents, calculates connected road exposure, downloads Overture buildings, calculates building exposure, estimates damage, and refreshes `data/processed/gis/regional_flood_comparison.csv`.

Use these coarse regional outputs for comparison and presentation tables. Use the 1-meter workflow for final high-confidence maps or building-level conclusions.

Useful Hampton Roads Census county-equivalent GEOIDs for `scripts/ingest_study_area.py` and `scripts/ingest_roads.py`:

```text
Norfolk:              51710
Virginia Beach:       51810
Chesapeake:           51550
Portsmouth:           51740
Hampton:              51650
Newport News:         51700
Suffolk:              51800
Poquoson:             51735
York County:          51199
James City County:    51095
Isle of Wight County: 51093
```

## Optional Coarse Baseline Workflow

The original 1 arc-second DEM workflow is retained as a lightweight baseline. Use it for quick testing, not final exposure outputs.

Search and download coarse USGS 3DEP tiles:

```bash
python scripts/download_usgs_dem.py \
  --study-area-id norfolk_va \
  --dataset "National Elevation Dataset (NED) 1 arc-second"

python scripts/download_usgs_dem.py \
  --study-area-id norfolk_va \
  --dataset "National Elevation Dataset (NED) 1 arc-second" \
  --download
```

Clip the coarse DEM:

```bash
python scripts/clip_dem_to_study_area.py \
  --study-area-id norfolk_va \
  --input-dem \
    data/raw/usgs_3dep/USGS_1_n37w077_20260324.tif \
    data/raw/usgs_3dep/USGS_1_n38w077_20260324.tif \
  --output data/processed/dem/norfolk_va_usgs_1arcsec_navd88_m.tif
```

Create coarse flood-depth rasters and connected products with the default output directories:

```bash
python scripts/create_flood_depth_rasters.py \
  --study-area-id norfolk_va \
  --dem data/processed/dem/norfolk_va_usgs_1arcsec_navd88_m.tif \
  --dem-units meters

python scripts/create_connected_flood_depth_rasters.py --min-depth-ft 0
python scripts/polygonize_connected_flood_extents.py --min-depth-ft 0
```

Current clipped coarse DEM validation:

```text
path=data/processed/dem/norfolk_va_usgs_1arcsec_navd88_m.tif
crs=EPSG:4269
shape=794x764
nodata=-999999.0
units=meters
vertical_datum=NAVD88, based on USGS 3DEP product convention
min_m=-3.610
max_m=20.738
mean_m=1.717
```

Coarse versus 1-meter connected-road comparison:

```text
current peak: coarse=13.397 mi 1-meter=12.439 mi
+1 ft SLR:    coarse=15.506 mi 1-meter=13.724 mi
+2 ft SLR:    coarse=20.627 mi 1-meter=19.770 mi
+3 ft SLR:    coarse=38.910 mi 1-meter=43.972 mi
```

## Troubleshooting

If setup or execution fails, check these first:

- `DATABASE_URL` missing or malformed: confirm `.env` exists in the repository root and uses the `postgresql+psycopg://...` format.
- PostGIS check fails: verify the database has PostGIS enabled and the user has schema/table privileges.
- DEM download finds no products: rerun the search command and confirm the study area was ingested first.
- DEM clip finds no files: confirm downloaded GeoTIFFs exist under `data/raw/usgs_3dep_1m/`.
- Flood-depth creation finds no scenarios: rerun NOAA ingest, scenario creation, and NAVD88 conversion.
- Connected products find no registered rasters: rerun `scripts/create_flood_depth_rasters.py` with the desired DEM/output directory.
- Building exposure returns no rows: confirm `processed.buildings`, `processed.connected_flood_extents`, and `results.connected_flood_depth_rasters` are populated.
- GIS export is missing building layers: run the building download, ingest, and exposure steps before exporting.

## Project Notes

- The 1-meter DEM workflow is the active workflow for exposure outputs.
- The 1 arc-second DEM is retained only as a quick baseline.
- Before comparing NOAA water levels against elevations, convert everything to a common vertical datum.
- The station datum conversion is appropriate for a screening-level project near Sewells Point/Norfolk. A broader Hampton Roads analysis should evaluate spatially varying tidal datums or VDatum.
- Connected flood extents use the 1-meter DEM but remain a screening-level static water-surface model, not a hydrodynamic simulation.
- Recommended next exposure layers are parcels/property, critical facilities, and population/social vulnerability.
