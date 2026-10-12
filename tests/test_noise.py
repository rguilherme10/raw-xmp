import numpy as np
import pytest

from rawxmp.analysis.noise import NoiseModel, estimate_noise_model
from rawxmp.config import load_config
from rawxmp.detail.detail import estimate_detail


def noisy_plane(a, b, seed=0, shape=(600, 600)):
    rng = np.random.default_rng(seed)
    base = np.clip(np.linspace(0.02, 0.8, shape[1])[None, :] + 0 * np.linspace(0, 1, shape[0])[:, None], 0, 1)
    sig = np.sqrt(a * base + b)
    return np.clip(base + rng.normal(0, 1, shape) * sig, 0, 1).astype(np.float32)


def test_estimator_recovers_shot_and_read_noise():
    m = estimate_noise_model(noisy_plane(4e-4, 4e-6))
    assert m.valid and 0.6 * 4e-4 < m.a < 1.5 * 4e-4 and m.b < 5e-5


def test_noisier_image_gives_larger_relative_noise():
    lo, hi = estimate_noise_model(noisy_plane(1e-5, 1e-7)), estimate_noise_model(noisy_plane(4e-4, 4e-6))
    assert hi.relative(0.05) > 3 * lo.relative(0.05)


def test_texture_is_not_mistaken_for_noise():
    clean = np.tile(np.linspace(0.1, 0.7, 600, dtype=np.float32), (600, 1))
    stripes = clean + 0.05 * (np.indices((600, 600)).sum(0) // 60 % 2)           # strong edges between flat areas, zero noise
    assert estimate_noise_model(stripes).relative(0.1) < 0.03


def test_invalid_input_is_reported_not_guessed():
    assert not estimate_noise_model(np.zeros((600, 600), np.float32)).valid
    assert not estimate_noise_model(np.zeros((10, 10), np.float32)).valid


def test_exposure_push_raises_noise_reduction():
    cfg = load_config()
    model = NoiseModel(a=2e-4, b=2e-6, valid=True, bins_used=12)
    flat = estimate_detail(800, cfg, model, 0.0)
    pushed = estimate_detail(800, cfg, model, 2.0)
    assert pushed["luminance_nr"].value > flat["luminance_nr"].value
    assert pushed["color_nr"].value > flat["color_nr"].value
    assert pushed["sharpness"].value < flat["sharpness"].value
    assert "ISO efetivo" in pushed["sharpness"].reason


def test_falls_back_to_iso_rule_without_a_valid_model():
    cfg = load_config()
    a = estimate_detail(3200, cfg, None, 0.0)
    b = estimate_detail(3200, cfg, NoiseModel(0, 0, False), 0.0)
    assert a["luminance_nr"].value == b["luminance_nr"].value > 0
    assert "não pôde ser medido" in a["luminance_nr"].reason


def test_nr_respects_configured_limits():
    cfg = load_config()
    lo, hi = cfg.limit("luminance_nr")
    huge = estimate_detail(25600, cfg, NoiseModel(a=0.05, b=1e-3, valid=True), 2.5)
    assert lo <= huge["luminance_nr"].value <= hi


def test_pipeline_reports_noise_and_scales_with_iso(decisions):
    assert decisions["high_iso"].params["LuminanceSmoothing"] > decisions["normal"].params["LuminanceSmoothing"]
    assert decisions["high_iso"].estimates["noise_model"].value["valid"]
    assert decisions["high_iso"].estimates["detail_noise_relative"].value > 3 * decisions["normal"].estimates["detail_noise_relative"].value
