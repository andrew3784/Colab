CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS processed;
CREATE SCHEMA IF NOT EXISTS results;

CREATE TABLE IF NOT EXISTS raw.noaa_stations (
    station_id text PRIMARY KEY,
    name text,
    latitude double precision,
    longitude double precision,
    state text,
    timezone text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    geom geometry(Point, 4326)
);

CREATE TABLE IF NOT EXISTS raw.noaa_water_levels (
    station_id text NOT NULL REFERENCES raw.noaa_stations(station_id),
    observed_at timestamptz NOT NULL,
    water_level double precision,
    sigma double precision,
    flags text,
    quality text,
    datum text NOT NULL,
    units text NOT NULL,
    product text NOT NULL DEFAULT 'water_level',
    source text NOT NULL DEFAULT 'NOAA CO-OPS',
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id, observed_at, datum, units, product)
);

CREATE INDEX IF NOT EXISTS noaa_water_levels_observed_at_idx
    ON raw.noaa_water_levels (observed_at);

CREATE TABLE IF NOT EXISTS raw.noaa_station_datums (
    station_id text NOT NULL REFERENCES raw.noaa_stations(station_id),
    datum_name text NOT NULL,
    description text,
    value double precision NOT NULL,
    units text NOT NULL,
    orthometric_datum text,
    epoch text,
    accepted text,
    superseded text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id, datum_name)
);

CREATE TABLE IF NOT EXISTS processed.flood_scenarios (
    scenario_id text PRIMARY KEY,
    name text NOT NULL,
    source_station_id text REFERENCES raw.noaa_stations(station_id),
    event_start timestamptz,
    event_end timestamptz,
    peak_observed_at timestamptz,
    peak_water_level double precision,
    water_surface_elevation double precision,
    analysis_datum text,
    analysis_water_surface_elevation double precision,
    datum text,
    units text,
    sea_level_rise_ft double precision DEFAULT 0,
    study_area text,
    method text,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE processed.flood_scenarios ADD COLUMN IF NOT EXISTS peak_observed_at timestamptz;
ALTER TABLE processed.flood_scenarios ADD COLUMN IF NOT EXISTS peak_water_level double precision;
ALTER TABLE processed.flood_scenarios ADD COLUMN IF NOT EXISTS water_surface_elevation double precision;
ALTER TABLE processed.flood_scenarios ADD COLUMN IF NOT EXISTS analysis_datum text;
ALTER TABLE processed.flood_scenarios ADD COLUMN IF NOT EXISTS analysis_water_surface_elevation double precision;
ALTER TABLE processed.flood_scenarios ADD COLUMN IF NOT EXISTS study_area text;
ALTER TABLE processed.flood_scenarios ADD COLUMN IF NOT EXISTS method text;

CREATE TABLE IF NOT EXISTS processed.study_areas (
    study_area_id text PRIMARY KEY,
    name text NOT NULL,
    source text,
    source_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    geom geometry(MultiPolygon, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS study_areas_geom_idx
    ON processed.study_areas USING gist (geom);

CREATE TABLE IF NOT EXISTS processed.flood_extents (
    id bigserial PRIMARY KEY,
    scenario_id text NOT NULL REFERENCES processed.flood_scenarios(scenario_id),
    min_depth_ft double precision,
    max_depth_ft double precision,
    created_at timestamptz NOT NULL DEFAULT now(),
    geom geometry(MultiPolygon, 4326)
);

CREATE INDEX IF NOT EXISTS flood_extents_geom_idx
    ON processed.flood_extents USING gist (geom);

CREATE TABLE IF NOT EXISTS results.flood_depth_rasters (
    scenario_id text PRIMARY KEY REFERENCES processed.flood_scenarios(scenario_id),
    study_area_id text REFERENCES processed.study_areas(study_area_id),
    dem_path text NOT NULL,
    raster_path text NOT NULL,
    water_surface_elevation_ft double precision NOT NULL,
    analysis_datum text NOT NULL,
    depth_units text NOT NULL DEFAULT 'feet',
    min_depth_ft double precision,
    max_depth_ft double precision,
    mean_depth_ft double precision,
    wet_pixel_count bigint,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS results.connected_flood_depth_rasters (
    scenario_id text PRIMARY KEY REFERENCES processed.flood_scenarios(scenario_id),
    study_area_id text REFERENCES processed.study_areas(study_area_id),
    source_raster_path text NOT NULL,
    raster_path text NOT NULL,
    min_depth_threshold_ft double precision NOT NULL DEFAULT 0,
    connectivity text NOT NULL DEFAULT 'edge-connected-8way',
    min_depth_ft double precision,
    max_depth_ft double precision,
    mean_depth_ft double precision,
    wet_pixel_count bigint,
    removed_wet_pixel_count bigint,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS processed.connected_flood_extents (
    id bigserial PRIMARY KEY,
    scenario_id text NOT NULL REFERENCES processed.flood_scenarios(scenario_id),
    min_depth_ft double precision,
    max_depth_ft double precision,
    created_at timestamptz NOT NULL DEFAULT now(),
    geom geometry(MultiPolygon, 4326)
);

CREATE INDEX IF NOT EXISTS connected_flood_extents_geom_idx
    ON processed.connected_flood_extents USING gist (geom);

CREATE TABLE IF NOT EXISTS processed.roads (
    study_area_id text NOT NULL REFERENCES processed.study_areas(study_area_id),
    road_id text NOT NULL,
    fullname text,
    rttyp text,
    mtfcc text,
    source text NOT NULL,
    source_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    geom geometry(MultiLineString, 4326) NOT NULL,
    PRIMARY KEY (study_area_id, road_id)
);

CREATE INDEX IF NOT EXISTS roads_geom_idx
    ON processed.roads USING gist (geom);

CREATE TABLE IF NOT EXISTS processed.buildings (
    study_area_id text NOT NULL REFERENCES processed.study_areas(study_area_id),
    building_id text NOT NULL,
    name text,
    building_type text,
    source text NOT NULL,
    source_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    geom geometry(MultiPolygon, 4326) NOT NULL,
    PRIMARY KEY (study_area_id, building_id)
);

CREATE INDEX IF NOT EXISTS buildings_geom_idx
    ON processed.buildings USING gist (geom);

CREATE TABLE IF NOT EXISTS results.road_flood_impacts (
    scenario_id text NOT NULL REFERENCES processed.flood_scenarios(scenario_id),
    study_area_id text NOT NULL REFERENCES processed.study_areas(study_area_id),
    road_id text NOT NULL,
    fullname text,
    mtfcc text,
    flooded_length_m double precision NOT NULL,
    flooded_length_mi double precision NOT NULL,
    max_depth_ft double precision,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scenario_id, study_area_id, road_id),
    FOREIGN KEY (study_area_id, road_id) REFERENCES processed.roads(study_area_id, road_id)
);

CREATE TABLE IF NOT EXISTS results.road_exposure_summary (
    scenario_id text NOT NULL REFERENCES processed.flood_scenarios(scenario_id),
    study_area_id text NOT NULL REFERENCES processed.study_areas(study_area_id),
    road_count bigint NOT NULL,
    flooded_road_count bigint NOT NULL,
    total_road_length_m double precision NOT NULL,
    flooded_length_m double precision NOT NULL,
    flooded_length_mi double precision NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scenario_id, study_area_id)
);

CREATE TABLE IF NOT EXISTS results.connected_road_flood_impacts (
    scenario_id text NOT NULL REFERENCES processed.flood_scenarios(scenario_id),
    study_area_id text NOT NULL REFERENCES processed.study_areas(study_area_id),
    road_id text NOT NULL,
    fullname text,
    mtfcc text,
    flooded_length_m double precision NOT NULL,
    flooded_length_mi double precision NOT NULL,
    max_depth_ft double precision,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scenario_id, study_area_id, road_id),
    FOREIGN KEY (study_area_id, road_id) REFERENCES processed.roads(study_area_id, road_id)
);

CREATE TABLE IF NOT EXISTS results.connected_road_exposure_summary (
    scenario_id text NOT NULL REFERENCES processed.flood_scenarios(scenario_id),
    study_area_id text NOT NULL REFERENCES processed.study_areas(study_area_id),
    road_count bigint NOT NULL,
    flooded_road_count bigint NOT NULL,
    total_road_length_m double precision NOT NULL,
    flooded_length_m double precision NOT NULL,
    flooded_length_mi double precision NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scenario_id, study_area_id)
);

CREATE TABLE IF NOT EXISTS results.connected_building_flood_impacts (
    scenario_id text NOT NULL REFERENCES processed.flood_scenarios(scenario_id),
    study_area_id text NOT NULL REFERENCES processed.study_areas(study_area_id),
    building_id text NOT NULL,
    name text,
    building_type text,
    footprint_area_m2 double precision NOT NULL,
    flooded_area_m2 double precision NOT NULL,
    flooded_area_fraction double precision NOT NULL,
    max_depth_ft double precision,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scenario_id, study_area_id, building_id),
    FOREIGN KEY (study_area_id, building_id) REFERENCES processed.buildings(study_area_id, building_id)
);

CREATE TABLE IF NOT EXISTS results.connected_building_exposure_summary (
    scenario_id text NOT NULL REFERENCES processed.flood_scenarios(scenario_id),
    study_area_id text NOT NULL REFERENCES processed.study_areas(study_area_id),
    building_count bigint NOT NULL,
    flooded_building_count bigint NOT NULL,
    total_footprint_area_m2 double precision NOT NULL,
    flooded_footprint_area_m2 double precision NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scenario_id, study_area_id)
);
