import pytest

from rawxmp.tone.curve import build_curve, enforce_monotonic
from rawxmp.skin.detector import SkinResult


def test_curves_valid_for_every_scenario(decisions, cfg):
    dev = cfg.get("limits.tone_curve_max_deviation")
    for k, d in decisions.items():
        pts = d.curve.points
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        assert xs == [0, 32, 64, 128, 192, 255], k
        assert ys[0] == 0 and ys[-1] == 255, k
        assert all(b > a for a, b in zip(ys, ys[1:])), f"{k}: not strictly increasing"
        assert all(abs(y - x) <= dev for x, y in pts), k


def test_curve_is_adaptive(decisions):
    curves = {tuple(d.curve.points) for d in decisions.values()}
    assert len(curves) >= 4                          # not always the same curve
    assert any(c != ((0, 0), (32, 32), (64, 64), (128, 128), (192, 192), (255, 255)) for c in curves)


def test_low_contrast_gets_steeper_s_than_high_contrast(decisions):
    lo, hi = decisions["low_contrast"].curve.points, decisions["high_contrast"].curve.points
    assert (lo[4][1] - lo[2][1]) > (hi[4][1] - hi[2][1])


def test_skin_slope_is_limited(decisions, cfg):
    lo, hi = cfg.get("tone_curve.skin_slope")
    for d in decisions.values():
        if d.skin.detected:
            (_, y1), (_, y2) = d.curve.points[3], d.curve.points[4]
            assert lo - 0.02 <= (y2 - y1) / 64 <= hi + 0.02


def test_enforce_monotonic_repairs_inversions():
    xs = [0, 32, 64, 128, 192, 255]
    ys = enforce_monotonic([0, 40, 30, 128, 120, 255], 0.35, xs)
    assert all(b > a for a, b in zip(ys, ys[1:])) and ys[-1] == 255


def test_unbounded_input_never_leaves_range(cfg_portrait, decisions):
    d = decisions["high_contrast"]
    est = dict(d.estimates)
    est["contrast"] = type(est["contrast"])(100, 1, "extreme")
    est["highlights"] = type(est["highlights"])(-100, 1, "extreme")
    r = build_curve(cfg_portrait, est, d.stats, SkinResult())
    assert all(0 <= y <= 255 for _, y in r.points)
    assert all(abs(y - x) <= cfg_portrait.get("limits.tone_curve_max_deviation") for x, y in r.points)
