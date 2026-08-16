from __future__ import annotations


FUTURE_SLR_DECADES_FT = {
    2030: 0.5,
    2040: 1.0,
    2050: 1.5,
    2060: 2.0,
    2070: 2.5,
    2080: 3.0,
    2090: 3.75,
    2100: 4.5,
}

STRESS_TEST_SLR_FT = [4.0, 5.0, 6.0]
FULL_SLR_SCENARIO_VALUES_FT = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.75, 4.0, 4.5, 5.0, 6.0]


def scenario_metadata(sea_level_rise_ft: float) -> dict[str, object]:
    value = round(float(sea_level_rise_ft), 2)
    decade_lookup = {round(v, 2): year for year, v in FUTURE_SLR_DECADES_FT.items()}
    planning_year = decade_lookup.get(value)
    if value == 0:
        scenario_type = "current_event"
        label = "Current event"
    elif planning_year is not None:
        scenario_type = "future_decade"
        label = f"{planning_year} (+{value:g} ft SLR)"
    else:
        scenario_type = "stress_view"
        label = f"+{value:g} ft SLR stress view"
    return {
        "planning_year": planning_year,
        "scenario_type": scenario_type,
        "scenario_label": label,
    }


def add_scenario_metadata(frame):
    if "sea_level_rise_ft" not in frame.columns:
        return frame
    metadata = frame["sea_level_rise_ft"].map(scenario_metadata)
    frame = frame.copy()
    frame["planning_year"] = metadata.map(lambda item: item["planning_year"])
    frame["scenario_type"] = metadata.map(lambda item: item["scenario_type"])
    frame["scenario_label"] = metadata.map(lambda item: item["scenario_label"])
    return frame
