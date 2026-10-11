import numpy as np
import pytest

from rawxmp.color.hsl import BANDS, analyze_bands, estimate_hsl
from rawxmp.colorspace import srgb_decode
from rawxmp.skin.detector import SkinResult, detect_skin


def solid(rgb255, shape=(120, 160)):
    c = srgb_decode(np.array(rgb255, np.float32) / 255.0)
    return np.broadcast_to(c, shape + (3,)).copy()


def scene_with_patch(rgb255, frac=0.08):
    img = solid((120, 120, 120))
    h, w = img.shape[:2]
    ph, pw = int(h * 0.5), int(w * frac / 0.5 * 1.0)
    img[20:20 + ph, 30:30 + max(pw, 20)] = srgb_decode(np.array(rgb255, np.float32) / 255.0)
    return img


def test_skin_detected_in_portrait(decisions):
    s = decisions["normal"].skin
    assert s.detected and 0.5 < s.confidence <= 0.85 and 38 <= s.hue_deg <= 70
    assert s.deviation == "none" and s.reason


def test_no_skin_in_scene_without_people(decisions):
    s = decisions["no_skin"].skin
    assert not s.detected and s.confidence == 0.0


def test_gray_and_blue_are_not_skin(cfg):
    assert not detect_skin(solid((128, 128, 128)), cfg).detected
    assert not detect_skin(solid((60, 90, 200)), cfg).detected


def test_confidence_capped_without_face(cfg):
    s = detect_skin(scene_with_patch((224, 172, 140), 0.3), cfg)
    assert s.detected and s.confidence <= cfg.get("skin.detector_confidence_cap")


def test_huge_skin_coloured_area_loses_confidence(cfg):
    big = detect_skin(solid((224, 172, 140)), cfg)
    assert (not big.detected) or big.confidence < 0.5


def test_tiny_patch_not_detected(cfg):
    img = solid((120, 120, 120))
    img[10:14, 10:14] = srgb_decode(np.array([224, 172, 140], np.float32) / 255.0)
    assert not detect_skin(img, cfg).detected


def test_skin_disabled_in_config(cfg_portrait):
    from rawxmp.config import Config
    c = Config({**cfg_portrait.data, "skin": {**cfg_portrait.data["skin"], "enabled": False}})
    assert not detect_skin(scene_with_patch((224, 172, 140)), c).detected


def test_skin_deviation_labels(cfg):
    red = detect_skin(scene_with_patch((235, 140, 120), 0.4), cfg)
    yellow = detect_skin(scene_with_patch((215, 180, 110), 0.4), cfg)
    for s in (red, yellow):
        if s.detected:
            assert s.deviation in ("none", "toward_red", "toward_magenta", "toward_yellow")
    if yellow.detected:
        assert yellow.deviation in ("toward_yellow", "none")
    if red.detected:
        assert red.deviation in ("toward_red", "toward_magenta", "none")


# --- HSL -------------------------------------------------------------------------------------
def test_bands_cover_all_eight_colours(cfg):
    b = analyze_bands(solid((40, 160, 40)), cfg)
    assert set(b) == set(BANDS)
    assert b["green"].fraction > 0.9 and b["red"].fraction < 0.05


def test_no_evidence_no_adjustment(cfg):
    img = solid((128, 128, 128))
    hsl = estimate_hsl(analyze_bands(img, cfg), SkinResult(), cfg)
    assert all(e.value == 0 for d in (hsl.hue, hsl.saturation, hsl.luminance) for e in d.values())


def test_oversaturated_colour_is_reduced(cfg):
    img = solid((0, 255, 20))
    hsl = estimate_hsl(analyze_bands(img, cfg), SkinResult(), cfg)
    assert hsl.saturation["green"].value < 0
    assert hsl.saturation["blue"].value == 0                        # absent band untouched


def test_moderate_colour_untouched(cfg):
    img = solid((90, 150, 90))
    hsl = estimate_hsl(analyze_bands(img, cfg), SkinResult(), cfg)
    assert hsl.saturation["green"].value == 0


def test_skin_protection_limits_warm_band_changes(cfg):
    img = solid((255, 120, 0))                                      # very saturated orange
    bands = analyze_bands(img, cfg)
    free = estimate_hsl(bands, SkinResult(), cfg).saturation["orange"].value
    skin = SkinResult(detected=True, confidence=0.9, hue_deg=55, deviation="none")
    prot = estimate_hsl(bands, skin, cfg).saturation["orange"].value
    assert free < 0 and abs(prot) <= cfg.get("color.skin_cap") and abs(prot) < abs(free)


def test_skin_drift_nudges_orange_hue(cfg):
    img = solid((128, 128, 128))
    skin = SkinResult(detected=True, confidence=0.9, hue_deg=30, deviation="toward_red", deviation_deg=-8)
    hsl = estimate_hsl(analyze_bands(img, cfg), skin, cfg)
    assert hsl.hue["orange"].value > 0
    assert abs(hsl.hue["orange"].value) <= cfg.get("color.skin_hue_nudge")


def test_hsl_values_are_integers_within_limits(decisions, cfg):
    lo, hi = cfg.limit("hsl_saturation")
    for d in decisions.values():
        for c in ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"):
            v = d.params[f"SaturationAdjustment{c}"]
            assert float(v) == int(v) and lo <= v <= hi


def test_dark_frame_skin_is_still_found_and_reports_real_luminance(cfg):
    bright = scene_with_patch((224, 172, 140), 0.3)
    dark = bright * 0.12                                  # same scene, ~3 stops underexposed
    a, b = detect_skin(bright, cfg), detect_skin(dark, cfg)
    assert a.detected and b.detected
    assert b.median_y == pytest.approx(a.median_y * 0.12, rel=0.25)   # luminance reported at the real exposure


def test_skin_debug_reports_criteria(cfg):
    dbg = {}
    detect_skin(scene_with_patch((224, 172, 140), 0.3), cfg, dbg)
    assert set(dbg["pass_fraction"]) == {"ycrcb", "lab_hue", "lab_chroma", "lab_l", "hsv_sat", "not_clipped"}
    assert set(dbg["rejected_only_by"]) == set(dbg["pass_fraction"])
