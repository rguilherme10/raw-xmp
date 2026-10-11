import numpy as np
import pytest

from rawxmp.analysis.stats import compute_stats
from rawxmp.exposure.estimator import estimate_exposure
from rawxmp.skin.detector import SkinResult


def flat(value, shape=(60, 80)):
    return np.full(shape + (3,), value, dtype=np.float32)


def ramp(lo, hi, shape=(60, 80)):
    g = np.linspace(lo, hi, shape[1], dtype=np.float32)
    return np.repeat(np.repeat(g[None, :, None], shape[0], 0), 3, 2)


def test_histogram_normalised_and_percentiles_ordered(cfg):
    s = compute_stats(ramp(0, 1), cfg)
    assert sum(s.histogram) == pytest.approx(1.0)
    v = [s.pct_y[k] for k in ("p0.1", "p1", "p5", "p50", "p95", "p99", "p99.9")]
    assert v == sorted(v)
    assert s.pct_y["p50"] == pytest.approx(0.5, abs=0.02)


def test_clipping_fractions(cfg):
    img = ramp(0, 0.5)
    img[:, -8:] = 1.0                       # 10 % of columns clipped
    s = compute_stats(img, cfg)
    assert s.near_white_fraction == pytest.approx(0.1, abs=0.01)
    assert s.clip_fraction_any_channel == pytest.approx(0.1, abs=0.01)
    assert s.near_black_fraction > 0.0


def test_near_black_fraction(cfg):
    img = ramp(0.0, 0.6)
    img[:, :16] = 0.0
    assert compute_stats(img, cfg).near_black_fraction >= 0.2


def test_global_and_local_contrast(cfg):
    flat_s = compute_stats(flat(0.2), cfg)
    assert flat_s.global_contrast == pytest.approx(0, abs=1e-3) and flat_s.local_contrast < 1e-3
    rng = np.random.default_rng(0)
    noisy = np.clip(0.2 + rng.normal(0, 0.1, (60, 80, 1)), 0, 1).repeat(3, 2).astype(np.float32)
    assert compute_stats(noisy, cfg).local_contrast > 0.05


@pytest.mark.parametrize("img,key", [
    (ramp(0.0, 0.12), "underexposed"),            # dark and nothing near white
    (ramp(0.4, 0.98), "high_key"),
])
def test_key_classification(cfg, img, key):
    assert compute_stats(img, cfg).key == key


def test_low_key_needs_real_highlights(cfg):
    img = flat(0.01)
    img[:4, :4] = 0.95                      # a small bright light source in a dark scene
    img[:, :, :] = np.maximum(img, 0.01)
    s = compute_stats(np.repeat(img[:, :, :1], 3, 2), cfg)
    assert s.key in ("low_key", "high_contrast")


def est(raw_set_stats, cfg, skin=None):
    return estimate_exposure(raw_set_stats, skin or SkinResult(), cfg)


def stats_for(decisions, k):
    return decisions[k].stats


def test_underexposed_gets_positive_exposure(decisions):
    d = decisions
    assert d["underexposed"].params["Exposure2012"] > 0.2
    assert d["underexposed"].params["Exposure2012"] > d["normal"].params["Exposure2012"] + 0.8   # skin dark → brighten, bright → not
    assert d["underexposed"].stats.key == "underexposed"


def test_overexposed_gets_negative_highlights_and_exposure(decisions):
    P = decisions["overexposed"].params
    assert P["Exposure2012"] <= 0 and P["Highlights2012"] <= -20 and P["Whites2012"] < 0


def test_high_contrast_recovers_both_ends(decisions):
    P = decisions["high_contrast"].params
    assert P["Highlights2012"] <= -30 and P["Shadows2012"] >= 20
    assert P["Contrast2012"] <= 0


def test_low_contrast_adds_contrast(decisions):
    assert decisions["low_contrast"].params["Contrast2012"] >= 20


def test_all_estimates_have_confidence_and_reason(decisions):
    for d in decisions.values():
        for k in ("exposure", "highlights", "shadows", "whites", "blacks", "contrast"):
            e = d.estimates[k]
            assert 0.0 <= e.confidence <= 1.0 and e.reason


def test_limits_always_respected(decisions, cfg):
    lim = {"Exposure2012": "exposure", "Contrast2012": "contrast", "Highlights2012": "highlights",
           "Shadows2012": "shadows", "Whites2012": "whites", "Blacks2012": "blacks"}
    for d in decisions.values():
        for p, l in lim.items():
            lo, hi = cfg.limit(l)
            assert lo <= d.params[p] <= hi


def test_no_aggressive_exposure_when_highlights_already_clipped(cfg):
    img = ramp(0.0, 1.0)
    img[:, 40:] = 1.0                       # half the frame blown
    s = compute_stats(img, cfg, sensor_clip_fraction=0.4)
    e = estimate_exposure(s, SkinResult(), cfg)
    assert e["exposure"].value <= 0.0
    assert e["highlights"].value < -20


def test_low_key_is_damped(cfg):
    img = ramp(0.0, 0.01)
    img[:10, :10] = 0.9
    img = np.maximum(img, 0.002)
    s = compute_stats(img, cfg)
    full = estimate_exposure(s, SkinResult(), cfg)["exposure"].value
    assert abs(full) <= 2.0
