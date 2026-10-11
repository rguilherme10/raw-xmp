"""Real cameras: unnormalised XYZ->cam matrices, orientation ambiguity (regression for tint -252 on a T6i)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import make_test_raws as gen  # noqa: E402

from rawxmp.colorspace import D65  # noqa: E402
from rawxmp.raw.reader import select_xyz_to_cam  # noqa: E402
from rawxmp.white_balance.estimator import neutral_to_kelvin_tint  # noqa: E402

# Unnormalised, Canon-like XYZ->camera matrix (rows R,G,B); D65 gives raw response ≠ (1,1,1)
M = np.array([[0.62, -0.08, -0.08], [-0.42, 1.17, 0.25], [-0.07, 0.18, 0.55]])


def fake_raw(matrix, delivered_transposed=False):
    n_d65 = M @ D65
    daylight = 1.0 / (n_d65 / n_d65[1])                       # LibRaw-style multipliers (R, G, B)
    mat4 = np.vstack([(M.T if delivered_transposed else M), np.zeros((1, 3))])
    return SimpleNamespace(rgb_xyz_matrix=mat4, color_matrix=np.zeros((3, 4)),
                           daylight_whitebalance=[*daylight, 0.0])


def neutral(cct):
    n = M @ gen.planck_xyz(cct)
    return n / n[1]


def test_orientation_as_delivered_is_verified():
    m, note = select_xyz_to_cam(fake_raw(M))
    assert np.allclose(m, M) and "verified" in note


def test_transposed_matrix_is_detected_and_fixed():
    m, note = select_xyz_to_cam(fake_raw(M, delivered_transposed=True))
    assert np.allclose(m, M) and "transposed" in note


def test_unverifiable_matrix_is_rejected():
    raw = fake_raw(M)
    raw.daylight_whitebalance = [1.0, 1.0, 1.0, 0.0]            # inconsistent with the matrix
    m, note = select_xyz_to_cam(raw)
    assert m is None and "unusable" in note


@pytest.mark.parametrize("cct", [3200, 4500, 5200, 6500, 8000])
def test_kelvin_tint_recovered_with_unnormalised_matrix(cct, cfg):
    k, tint = neutral_to_kelvin_tint(neutral(cct), M, cfg)
    assert abs(k - cct) / cct < 0.03 and abs(tint) < 15


def test_daylight_neutral_maps_to_6500k_zero_tint(cfg):
    n = M @ D65
    k, tint = neutral_to_kelvin_tint(n / n[1], M, cfg)
    assert abs(k - 6504) < 250 and abs(tint + 9.6) < 2      # D65 sits ~0.003 Duv above the Planckian locus
