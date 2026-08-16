import numpy as np

from flood_analysis.rasters import boundary_connected_wet_mask


def test_boundary_connected_wet_mask_keeps_diagonal_connection():
    wet = np.eye(4, dtype=bool)
    valid = np.ones_like(wet)

    connected = boundary_connected_wet_mask(wet, valid)

    np.testing.assert_array_equal(connected, wet)


def test_boundary_connected_wet_mask_removes_isolated_depression():
    wet = np.zeros((5, 5), dtype=bool)
    wet[0, 0] = True
    wet[2, 2] = True
    valid = np.ones_like(wet)

    connected = boundary_connected_wet_mask(wet, valid)

    expected = np.zeros_like(wet)
    expected[0, 0] = True
    np.testing.assert_array_equal(connected, expected)


def test_boundary_connected_wet_mask_seeds_next_to_nodata():
    wet = np.zeros((5, 5), dtype=bool)
    wet[2, 2] = True
    valid = np.ones_like(wet)
    valid[2, 1] = False

    connected = boundary_connected_wet_mask(wet, valid)

    np.testing.assert_array_equal(connected, wet)


def test_boundary_connected_wet_mask_handles_empty_input():
    wet = np.zeros((3, 3), dtype=bool)

    connected = boundary_connected_wet_mask(wet, np.ones_like(wet))

    np.testing.assert_array_equal(connected, wet)
