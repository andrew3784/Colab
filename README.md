
# Hampton Roads Flood Analysis, Old Dominion Univ., Summer 2026

Andrew Mounsey amounsey@odu.edu 

Ryan Thompson rthom035@odu.com

Scott Zumwalt jzum001@odu.edu


Python-first workflow for ingesting NOAA water-level data, storing project data in Postgres/PostGIS, and producing GIS-ready flood-analysis outputs for ArcGIS.

## Setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

Keep database credentials in `.env`:

```bash
DATABASE_URL=postgresql+psycopg://user:password@host:5432/database
```

## Verify PostGIS

```bash
python scripts/check_postgis.py
```

If that fails, check the connected user's privileges:

```bash
python scripts/check_db_privileges.py
```

The database owner may need to run `sql/bootstrap.sql` or grant this user permission to create schemas/tables.

## Initialize Tables

```bash
python scripts/init_db.py
```

## Ingest NOAA Water Levels

If database writes are not available yet, fetch NOAA data to CSV:

```bash
python scripts/fetch_noaa_water_levels.py \
  --station 8638610 \
  --begin-date 20260627 \
  --end-date 20260628
```

Once database permissions are ready, write the same NOAA data into PostGIS.

Example using Sewells Point, VA (`8638610`):

```bash
python scripts/ingest_noaa_water_levels.py \
  --station 8638610 \
  --begin-date 20260627 \
  --end-date 20260628 \
  --datum MLLW \
  --units english \
  --interval 6
```

The script writes observations to `raw.noaa_water_levels` and stores station metadata in `raw.noaa_stations`.

## Create Norfolk Pilot Scenarios

After NOAA observations are ingested, create flood scenarios from the peak observed water level. The default sea-level-rise variants are current conditions plus `1`, `2`, and `3` feet.

```bash
python scripts/create_peak_scenarios.py \
  --event-start 2026-06-27T00:00:00Z \
  --event-end 2026-06-28T23:59:59Z \
  --study-area "Norfolk pilot"
```

This writes rows to `processed.flood_scenarios`, including:

```text
peak_water_level
peak_observed_at
sea_level_rise_ft
water_surface_elevation
datum
units
study_area
method
```

Current caveat: the first scenarios use NOAA `MLLW` values because that is how the sample data was ingested. Do not compare these directly to a DEM unless the DEM and water levels have been converted to the same vertical datum, usually `NAVD88`.

## Convert Scenarios To NAVD88

Ingest NOAA station datum offsets:

```bash
python scripts/ingest_noaa_datums.py --station 8638610
```

Convert scenario water surfaces from `MLLW` to `NAVD88` using the station offsets:

```bash
python scripts/convert_scenarios_datum.py \
  --station 8638610 \
  --source-datum MLLW \
  --target-datum NAVD88
```

For Sewells Point (`8638610`), NOAA reports:

```text
MLLW = 4.38 ft above station datum
NAVD88 = 5.99 ft above station datum
```

So for this station epoch:

```text
NAVD88 elevation = MLLW water level - 1.61 ft
```

The Norfolk pilot scenarios currently resolve to:

```text
current peak: 1.536 ft NAVD88
+1 ft SLR:    2.536 ft NAVD88
+2 ft SLR:    3.536 ft NAVD88
+3 ft SLR:    4.536 ft NAVD88
```

Use `analysis_water_surface_elevation` and `analysis_datum` for DEM comparisons, not the original `water_surface_elevation` and `datum` fields.

## Ingest Norfolk Study Area

Load the Norfolk city boundary from US Census TIGER/Line county-equivalent boundaries:

```bash
python scripts/ingest_study_area.py \
  --study-area-id norfolk_va \
  --geoid 51710 \
  --name "Norfolk, VA"
```

This writes a valid `MultiPolygon` to `processed.study_areas`. The current Norfolk pilot geometry is approximately:

```text
249.68 sq km
96.40 sq mi
```

Use this geometry to clip DEM/elevation rasters and to constrain later exposure overlays.

## Download And Clip DEM

The pilot DEM workflow uses USGS 3DEP through The National Map API. The default script uses 1 arc-second GeoTIFFs because they are small enough for quick iteration. For final exposure mapping, prefer a higher-resolution lidar-derived DEM if schedule and storage allow.

Search for intersecting DEM tiles:

```bash
python scripts/download_usgs_dem.py \
  --study-area-id norfolk_va \
  --dataset "National Elevation Dataset (NED) 1 arc-second"
```

Download the latest GeoTIFF for each intersecting tile:

```bash
python scripts/download_usgs_dem.py \
  --study-area-id norfolk_va \
  --dataset "National Elevation Dataset (NED) 1 arc-second" \
  --download
```

Current Norfolk pilot tiles:

```text
data/raw/usgs_3dep/USGS_1_n37w077_20260324.tif
data/raw/usgs_3dep/USGS_1_n38w077_20260324.tif
```

Clip/merge the tiles to the Norfolk boundary:

```bash
python scripts/clip_dem_to_study_area.py \
  --study-area-id norfolk_va \
  --input-dem \
    data/raw/usgs_3dep/USGS_1_n37w077_20260324.tif \
    data/raw/usgs_3dep/USGS_1_n38w077_20260324.tif \
  --output data/processed/dem/norfolk_va_usgs_1arcsec_navd88_m.tif
```

Current clipped DEM validation:

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

## Higher-Resolution DEM Option

USGS 3DEP 1-meter products are available for Norfolk. The recommended local product set is `VA_HamptonRoads_B23`, published `2026-03-24`.

Search for the preferred 1-meter tiles:

```bash
python scripts/download_usgs_dem.py \
  --study-area-id norfolk_va \
  --dataset "Digital Elevation Model (DEM) 1 meter" \
  --title-contains VA_HamptonRoads_B23 \
  --max-results 100
```

Current search result:

```text
product_count=9
total_bytes=2361737796
```

Download the preferred 1-meter tiles when storage/time allow:

```bash
python scripts/download_usgs_dem.py \
  --study-area-id norfolk_va \
  --dataset "Digital Elevation Model (DEM) 1 meter" \
  --title-contains VA_HamptonRoads_B23 \
  --max-results 100 \
  --output-dir data/raw/usgs_3dep_1m \
  --download
```

Then clip to Norfolk:

```bash
python scripts/clip_dem_to_study_area.py \
  --study-area-id norfolk_va \
  --input-dem data/raw/usgs_3dep_1m/*.tif \
  --output data/processed/dem/norfolk_va_usgs_1m_hamptonroads_b23_navd88_m.tif
```

After clipping, rerun the depth, connectivity, road exposure, and GIS export steps with the 1-meter DEM. This should be the preferred path for final maps and exposure tables.

Intermediate option: 1/3 arc-second 3DEP is also available, but the current Norfolk search returns two tiles totaling about `823 MB`. If storage is available, use the 1-meter Hampton Roads product instead.

## Create Flood-Depth Rasters

Create one flood-depth GeoTIFF per NAVD88 scenario:

```bash
python scripts/create_flood_depth_rasters.py \
  --study-area-id norfolk_va \
  --dem data/processed/dem/norfolk_va_usgs_1arcsec_navd88_m.tif \
  --dem-units meters
```

Outputs are written under:

```text
data/processed/flood_depths/
```

Raster paths and summary stats are also registered in `results.flood_depth_rasters`.

Current depth-raster summary:

```text
current peak: wet_pixels=144850 max_depth_ft=13.381 mean_depth_ft=2.232
+1 ft SLR:    wet_pixels=147290 max_depth_ft=14.381 mean_depth_ft=3.187
+2 ft SLR:    wet_pixels=150076 max_depth_ft=15.381 mean_depth_ft=4.118
+3 ft SLR:    wet_pixels=155142 max_depth_ft=16.381 mean_depth_ft=4.968
```

## Polygonize Flood Extents

Convert wet pixels from registered depth rasters into vector flood extents:

```bash
python scripts/polygonize_flood_extents.py --min-depth-ft 0
```

This writes scenario polygons to `processed.flood_extents`, which is useful for fast map display and later exposure overlays.

Current vector extent summary:

```text
current peak: valid=True area_sq_km=110.464 max_depth_ft=13.381
+1 ft SLR:    valid=True area_sq_km=112.326 max_depth_ft=14.381
+2 ft SLR:    valid=True area_sq_km=114.452 max_depth_ft=15.381
+3 ft SLR:    valid=True area_sq_km=118.319 max_depth_ft=16.381
```

ArcGIS/QGIS can read the GeoTIFF depth rasters directly from `data/processed/flood_depths/`. Vector extents can be loaded from PostGIS table `processed.flood_extents`.

Important limitation: the current depth and extent products are a simple bathtub model. They include low inland depressions and existing water areas unless filtered later. Add hydrologic connectivity filtering before treating these as final inundation extents.

## Connectivity-Filtered Flood Extents

Create connected flood-depth rasters from the bathtub depth rasters:

```bash
python scripts/create_connected_flood_depth_rasters.py --min-depth-ft 0
```

This keeps wet cells connected by 8-neighbor connectivity to either the raster edge or the clipped study-area/nodata boundary. It removes isolated low depressions that are not connected to the modeled boundary water source.

Connected depth rasters are written under:

```text
data/processed/flood_depths_connected/
```

Raster metadata and removed-pixel counts are registered in `results.connected_flood_depth_rasters`.

Current connected raster summary:

```text
current peak: wet_pixels=143580 removed_wet_pixels=1270
+1 ft SLR:    wet_pixels=145546 removed_wet_pixels=1744
+2 ft SLR:    wet_pixels=147370 removed_wet_pixels=2706
+3 ft SLR:    wet_pixels=151783 removed_wet_pixels=3359
```

Polygonize connected rasters:

```bash
python scripts/polygonize_connected_flood_extents.py --min-depth-ft 0
```

This writes vector extents to `processed.connected_flood_extents` while preserving the original bathtub extents in `processed.flood_extents`.

Connectivity caveat: this is a screening-level boundary-connectivity filter, not a hydrodynamic model. It does not simulate flow paths, culverts, barriers, drainage, tide timing, or wave effects.

## Exposure Layers To Add Next

## Road Exposure Analysis

Ingest Census TIGER roads clipped to the Norfolk study area:

```bash
python scripts/ingest_roads.py \
  --study-area-id norfolk_va \
  --geoid 51710
```

Calculate flooded road length by scenario using `processed.flood_extents`:

```bash
python scripts/calculate_road_exposure.py --study-area-id norfolk_va
```

This writes feature-level impacts to `results.road_flood_impacts` and scenario summaries to `results.road_exposure_summary`.

Calculate road exposure against connectivity-filtered extents:

```bash
python scripts/calculate_road_exposure.py \
  --study-area-id norfolk_va \
  --connected
```

This writes feature-level impacts to `results.connected_road_flood_impacts` and scenario summaries to `results.connected_road_exposure_summary`.

Current Norfolk road exposure summary:

```text
road_count=6663
bathtub_road_impact_rows=1437

current peak: flooded_road_count=167 flooded_length_mi=16.256
+1 ft SLR:    flooded_road_count=238 flooded_length_mi=19.759
+2 ft SLR:    flooded_road_count=402 flooded_length_mi=27.178
+3 ft SLR:    flooded_road_count=630 flooded_length_mi=50.172
```

Current connected road exposure summary:

```text
connected_road_impact_rows=1048

current peak: flooded_road_count=121 flooded_length_mi=13.397
+1 ft SLR:    flooded_road_count=179 flooded_length_mi=15.506
+2 ft SLR:    flooded_road_count=280 flooded_length_mi=20.627
+3 ft SLR:    flooded_road_count=468 flooded_length_mi=38.910
```

Bathtub versus connected comparison:

```text
current peak: bathtub=16.256 mi connected=13.397 mi
+1 ft SLR:    bathtub=19.759 mi connected=15.506 mi
+2 ft SLR:    bathtub=27.178 mi connected=20.627 mi
+3 ft SLR:    bathtub=50.172 mi connected=38.910 mi
```

Export GIS layers for ArcGIS/QGIS review:

```bash
python scripts/export_gis_layers.py \
  --study-area-id norfolk_va \
  --output data/processed/gis/norfolk_flood_road_exposure.gpkg
```

The GeoPackage currently contains:

```text
roads
flood_extents
road_flood_impacts
connected_flood_extents
connected_road_flood_impacts
```

Road exposure caveats:

- TIGER roads are a generalized public baseline, not a routable transportation network.
- The connected outputs reduce disconnected-depression artifacts but are still screening-level.
- Use VGIN/local centerlines and a higher-resolution DEM before final transportation conclusions.

## Exposure Layers To Add Next

Recommended next exposure layers for the Norfolk pilot:

- Parcels/property: Norfolk or Virginia parcel data if licensing permits project use.
- Critical facilities: hospitals, schools, emergency services, wastewater/power infrastructure.
- Population/social vulnerability: Census blocks/block groups and CDC/ATSDR SVI.

## Building Exposure Analysis

Building exposure tables are available but no building footprint source has been loaded yet in the current database.

Preferred ingestion path: use a local building footprint file from Norfolk open data, Microsoft/Overture, VGIN, or another licensed source.

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

The script clips footprints to `processed.study_areas.study_area_id = 'norfolk_va'` and writes them to `processed.buildings`.

Experimental OSM ingestion is also available:

```bash
python scripts/ingest_buildings.py --study-area-id norfolk_va
```

The full Norfolk OSM/Overpass import is large and timed out in this environment, so use the file-based ingestion path for reliable project work.

After buildings are loaded, calculate building exposure against connected flood extents:

```bash
python scripts/calculate_building_exposure.py --study-area-id norfolk_va
```

This writes feature-level impacts to `results.connected_building_flood_impacts` and scenario summaries to `results.connected_building_exposure_summary`.

The GIS export script automatically includes these layers when building data exists:

```text
buildings
connected_building_flood_impacts
```

Current building table status:

```text
processed.buildings=0
results.connected_building_flood_impacts=0
results.connected_building_exposure_summary=0
```

## Project Notes

- Use PostGIS for shared vector/tabular data and scenario outputs.
- Keep large DEM/flood-depth rasters as GeoTIFF files outside the database.
- Before comparing NOAA water levels against elevations, convert everything to a common vertical datum.
- The initial pilot area is Norfolk so the DEM and exposure workflow stays small enough to validate before expanding region-wide.
- The station datum conversion is appropriate for a screening-level project near Sewells Point/Norfolk. A larger Hampton Roads analysis should evaluate spatially varying tidal datums or VDatum.
- The current 1 arc-second DEM is appropriate for workflow validation, not high-confidence parcel/building impacts.
- Flood-depth rasters are stored as files; PostGIS stores scenario metadata, raster paths, stats, study areas, and vectorized flood extents.
- Road exposure results are baseline screening outputs until the inundation layer is connectivity-filtered and road source quality is improved.
- Connected flood extents are available as a better default for screening maps than raw bathtub extents, but both remain limited by the coarse DEM and simplified water-surface model.
