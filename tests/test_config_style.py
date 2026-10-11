import pytest

from rawxmp.config import Config, ROOT, list_styles, load_config


def test_every_limit_has_min_le_max(cfg):
    for name, v in cfg.section("limits").items():
        if isinstance(v, dict):
            assert v["min"] <= v["max"], name


def test_style_overrides_base_but_keeps_other_values():
    base, port = load_config(), load_config("portrait")
    assert base.get("style.camera_profile") == "Adobe Standard"
    assert port.get("style.camera_profile") == "Camera Portrait"
    assert port.get("style.hsl.saturation.orange") == -3
    assert port.get("style.hsl.saturation.blue") == 0            # inherited from base
    assert port.get("limits.exposure.max") == base.get("limits.exposure.max")


def test_all_shipped_styles_load_and_have_valid_curves():
    for s in list_styles():
        c = load_config(s)
        pts = c.get("style.tone_curve.points")
        assert pts[0] == [0, 0] and pts[-1] == [255, 255]
        assert all(b[1] > a[1] for a, b in zip(pts, pts[1:])), s


def test_unknown_or_malicious_style_name():
    with pytest.raises(FileNotFoundError):
        load_config("does_not_exist")
    with pytest.raises(ValueError):
        load_config("../../etc/passwd")


def test_user_config_file_overrides(tmp_path):
    f = tmp_path / "u.yaml"
    f.write_text("limits:\n  exposure: {min: -0.5, max: 0.5}\n", encoding="utf-8")
    c = load_config(config_path=f)
    assert c.limit("exposure") == (-0.5, 0.5) and c.limit("contrast") == load_config().limit("contrast")


def test_camera_specific_rules_apply():
    c = load_config()
    assert c.with_camera("Canon EOS 5D Mark IV").camera_profile == "canon_generic"
    assert c.with_camera("Nikon D850").camera_profile is None
    assert c.with_camera(None) is c


def test_limits_actually_clamp_exposure(raw_set, tmp_path):
    from rawxmp.correction.engine import analyze_raw
    f = tmp_path / "u.yaml"
    f.write_text("limits:\n  exposure: {min: -0.1, max: 0.1}\n", encoding="utf-8")
    d = analyze_raw(raw_set["underexposed"], load_config(config_path=f))
    assert d.params["Exposure2012"] == 0.1


def test_style_is_constant_while_correction_varies(decisions):
    """STYLE = same look for all photos; IMAGE CORRECTION = differs per photo."""
    style_keys = ["CameraProfile", "Clarity2012", "Texture", "SharpenRadius", "SharpenDetail",
                  "ColorNoiseReductionDetail", "ParametricShadows", "ColorGradeBlending"]
    for k in style_keys:
        assert len({d.params[k] for d in decisions.values()}) == 1, k
    assert len({d.params["Exposure2012"] for d in decisions.values()}) > 3
    assert len({d.params["Highlights2012"] for d in decisions.values()}) > 2


def test_iso_adaptive_detail(decisions):
    low, high = decisions["normal"].params, decisions["high_iso"].params
    assert high["LuminanceSmoothing"] > low["LuminanceSmoothing"]
    assert high["Sharpness"] < low["Sharpness"] and high["SharpenEdgeMasking"] > low["SharpenEdgeMasking"]
