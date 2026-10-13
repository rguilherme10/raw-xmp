"""Regression on recorded results of the user's own 57 CR2+XMP pairs (Canon T6i; paths stripped)."""
import csv
from pathlib import Path

import numpy as np

from rawxmp.config import load_config
from rawxmp.types import clamp

ROWS = list(csv.DictReader(open(Path(__file__).parent / "data" / "real_rows_t6i.csv", encoding="utf-8")))


def test_measured_noise_never_asks_for_more_luminance_nr_than_the_user_applies():
    d = load_config().section("detail")
    human = np.array([float(r["LuminanceSmoothing_human"]) for r in ROWS])
    assert set(human) == {0.0}                                   # the user applies no luminance NR at all
    extra = [clamp((float(r["noise_rel"]) - d["noise_free_relative"]) * d["noise_gain"], 0, 100) for r in ROWS]
    assert max(extra) == 0.0                                      # measured 3.2–7.8 % relative noise stays below the free level


def test_recorded_exposure_errors_by_group_document_the_calibration_target():
    e = np.array([float(r["Exposure2012_algo"]) - float(r["Exposure2012_human"]) for r in ROWS])
    skin = np.array([int(r["skin_led"]) for r in ROWS]) == 1
    assert abs(e[skin].mean()) < 0.1 and abs(e[skin]).mean() < 0.3          # skin-led photos were already well calibrated
    assert e[~skin].mean() < -0.2                                           # no-skin photos were too dark → target_median 0.14 → 0.17
    assert load_config().get("exposure.target_median") >= 0.17


# ---- v2: run with the user's session style (57 pairs) --------------------------------------------------------
def _rows_v2():
    fn = Path(__file__).parent / "data" / "real_rows_t6i_v2.csv"
    out = []
    for r in csv.DictReader(open(fn, encoding="utf-8")):
        out.append({k: (float(v) if k not in ("file",) and v not in ("",) else v) for k, v in r.items()})
    return out


def test_calibrate_finds_that_the_user_ignores_image_driven_contrast():
    from rawxmp.calibration.calibrate import tonal_gains
    from rawxmp.config import Config
    cfg = load_config()
    cfg = Config({**cfg.data, "style": {**cfg.data["style"], "contrast_bias": 12, "highlights_bias": -34,
                                        "shadows_bias": 58, "whites_bias": -24, "blacks_bias": -61}})
    gains, limits, warns = tonal_gains(_rows_v2(), cfg)
    assert gains["contrast"] < 0.1                    # user holds Contrast ~constant: image estimate is noise
    assert 0 <= gains["highlights"] < 0.5             # follows Highlights only weakly
    assert "limits.blacks" in limits and limits["limits.blacks"]["min"] <= -100 + 9      # default -25 clipped the look (human -61..-91)
    assert any("Blacks2012" in w and "limite" in w for w in warns)


def test_estimate_gain_scales_only_the_image_part(decisions, cfg):
    from rawxmp.config import Config
    from rawxmp.correction.engine import analyze_raw
    import tempfile, yaml
    base = decisions["high_contrast"]
    zero = Config({**cfg.data, "exposure": {**cfg.data["exposure"], "estimate_gain": {"contrast": 0, "highlights": 0, "shadows": 0, "whites": 0, "blacks": 0}}})
    d0 = analyze_raw(base.info.path, zero)
    assert d0.params["Highlights2012"] == 0 and d0.params["Shadows2012"] == 0       # style bias is 0 in the base style
    assert base.params["Highlights2012"] < 0                                         # full gain keeps the measured correction


# ---- highlight anchor (57 real pairs: features + rows) ---------------------------------------------------------
def _anchor_rows():
    fn = Path(__file__).parent / "data" / "real_anchor_t6i.csv"
    return [{"file": r["file"], "skin_conf": float(r["skin_conf"]), "y_p99": float(r["y_p99"]),
             "Exposure2012_algo": float(r["Exposure2012_algo"]), "Exposure2012_human": float(r["Exposure2012_human"])}
            for r in csv.DictReader(open(fn, encoding="utf-8"))]


def test_highlight_anchor_is_fitted_and_validated_on_real_pairs():
    from rawxmp.calibration.calibrate import fit_highlight_anchor
    a = fit_highlight_anchor(_anchor_rows(), load_config())
    assert a["n"] == 57 and a["recommended"]
    assert -0.8 < a["slope"] < -0.35 and 0.0 < a["intercept"] < 0.3            # human ~ 0.09 - 0.55*log2(p99)
    assert a["mae_with_loo"] < a["mae_without"] - 0.02                          # leave-one-out, not in-sample


def test_anchor_needs_enough_individual_edits():
    from rawxmp.calibration.calibrate import fit_highlight_anchor
    assert fit_highlight_anchor(_anchor_rows()[:10], load_config()) is None


def test_anchor_off_by_default_and_blends_only_when_skin_is_not_in_charge(decisions, cfg):
    from rawxmp.config import Config
    from rawxmp.exposure.estimator import estimate_exposure
    from rawxmp.skin.detector import SkinResult
    assert cfg.get("exposure.highlight_anchor.enabled") is False
    on = Config({**cfg.data, "exposure": {**cfg.data["exposure"], "highlight_anchor": {"enabled": True, "slope": -1.0, "intercept": 2.0, "weight": 0.9}}})
    st = decisions["no_skin"].stats
    off_v = estimate_exposure(st, SkinResult(), cfg)["exposure"].value
    on_v = estimate_exposure(st, SkinResult(), on)
    assert on_v["exposure"].value != off_v and "ancora" in on_v["exposure"].reason
    confident = SkinResult(detected=True, confidence=0.9, median_y=0.12)
    a = estimate_exposure(st, confident, cfg)["exposure"].value
    b = estimate_exposure(st, confident, on)["exposure"].value
    assert a == b                                                               # confident skin: the anchor stays out
