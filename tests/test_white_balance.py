import numpy as np
import pytest

from rawxmp.raw.reader import RawInfo
from rawxmp.white_balance.estimator import (estimate_white_balance, find_neutral_candidates,
                                            gains_to_srgb_matrix, srgb_to_camera_matrix)


def test_normal_scene_keeps_as_shot(decisions):
    wb = decisions["normal"].wb
    assert wb.mode == "As Shot" and wb.temperature.confidence >= 0.7
    assert 5000 < wb.as_shot_temperature < 5400            # synthetic illuminant is 5200 K
    assert "Temperature" not in decisions["normal"].params


def test_warm_cast_is_cooled_but_bounded(decisions, cfg):
    d = decisions["warm_cast"]
    assert d.wb.mode == "Custom"
    delta = d.params["Temperature"] - d.wb.as_shot_temperature
    assert -cfg.get("white_balance.max_shift_kelvin") - 1 <= delta < -100
    assert d.params["WhiteBalance"] == "Custom"


def test_cool_cast_is_warmed_but_bounded(decisions, cfg):
    d = decisions["cool_cast"]
    delta = d.params["Temperature"] - d.wb.as_shot_temperature
    assert 100 < delta <= cfg.get("white_balance.max_shift_kelvin") + 1


def test_estimate_agrees_with_true_illuminant(decisions):
    assert abs(decisions["warm_cast"].wb.evidence["estimated_kelvin"] - 3600) < 400
    assert abs(decisions["cool_cast"].wb.evidence["estimated_kelvin"] - 8200) < 600


def test_confidence_is_reported(decisions):
    for d in decisions.values():
        assert 0 <= d.wb.temperature.confidence <= 1 and d.wb.temperature.reason


def test_tint_within_limits(decisions, cfg):
    lo, hi = cfg.limit("tint")
    for d in decisions.values():
        if "Tint" in d.params:
            assert lo <= d.params["Tint"] <= hi


def test_no_camera_data_means_as_shot(cfg):
    info = RawInfo(path="x", file_name="x.CR2", file_size=1, width=10, height=10)   # no WB, no matrix
    wb = estimate_white_balance(np.full((20, 20, 3), 0.3, np.float32), info, cfg)
    assert wb.mode == "As Shot" and wb.temperature.confidence < 0.5


def test_implausible_matrix_mapping_is_distrusted(cfg):
    info = RawInfo(path="x", file_name="x.CR2", file_size=1, width=10, height=10, camera_wb=[0.2, 1.0, 5.0],
                   xyz_to_cam=np.eye(3).tolist())
    wb = estimate_white_balance(np.full((20, 20, 3), 0.3, np.float32), info, cfg)
    assert wb.mode == "As Shot" and wb.evidence.get("mapping_distrusted") or wb.temperature.confidence <= 0.5


def test_few_neutral_pixels_means_no_change(raw_set, cfg):
    # saturated colour field: no neutral cluster at all
    from rawxmp.raw.reader import RawReader
    info = RawReader(raw_set["normal"]).read_info()
    img = np.zeros((60, 80, 3), np.float32)
    img[..., 0] = 0.5
    img[..., 1] = 0.05
    wb = estimate_white_balance(img, info, cfg)
    assert wb.mode == "As Shot"


def test_gain_matrix_identity_for_unit_gains():
    xyz_to_cam = np.array([[0.9, -0.2, -0.1], [-0.35, 1.15, 0.2], [0.02, 0.1, 0.65]])
    assert np.allclose(gains_to_srgb_matrix(np.ones(3), xyz_to_cam), np.eye(3), atol=1e-9)
    C = srgb_to_camera_matrix(xyz_to_cam)
    assert np.allclose(C.sum(axis=1), 1.0)


def test_neutral_candidates_found_in_gray_scene(cfg):
    img = np.full((60, 80, 3), 0.25, np.float32)
    mask, _ = find_neutral_candidates(img, cfg)
    assert mask.mean() > 0.9
