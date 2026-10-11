from rawxmp.validation.xmp_validator import validate_file, validate_model
from rawxmp.xmp.builder import build_model
from rawxmp.xmp.model import XMPModel
from rawxmp.xmp.serializer import XMPSerializer


def good(decisions, cfg, key="normal"):
    d = decisions[key]
    return build_model(d.params, d.curves, cfg)


def test_generated_xmps_validate(decisions, cfg):
    for k in decisions:
        rep = validate_model(good(decisions, cfg, k), cfg)
        assert rep.ok, (k, rep.errors)


def test_out_of_schema_range_is_error(decisions, cfg):
    m = good(decisions, cfg)
    m.attrs["Exposure2012"] = "+9.00"
    assert any("Exposure2012" in e for e in validate_model(m, cfg).errors)


def test_config_limits_are_enforced(decisions, cfg):
    m = good(decisions, cfg)
    m.attrs["Contrast2012"] = "+90"                       # valid for Camera Raw, beyond our limit (40)
    rep = validate_model(m, cfg)
    assert any("limites configurados" in e for e in rep.errors)
    assert validate_model(m, cfg, enforce_limits=False).ok


def test_non_numeric_and_choice_errors(decisions, cfg):
    m = good(decisions, cfg)
    m.attrs["Tint"] = "lots"
    m.attrs["WhiteBalance"] = "Moonlight"
    errs = " ".join(validate_model(m, cfg).errors)
    assert "Tint" in errs and "WhiteBalance" in errs


def test_non_monotonic_curve_is_error(decisions, cfg):
    m = good(decisions, cfg)
    m.curves["ToneCurvePV2012"] = [(0, 0), (64, 100), (128, 90), (255, 255)]
    assert any("monotônica" in e for e in validate_model(m, cfg).errors)
    m.curves["ToneCurvePV2012"] = [(0, 0), (128, 128), (128, 140), (255, 255)]
    assert any("crescente" in e for e in validate_model(m, cfg).errors)
    m.curves["ToneCurvePV2012"] = [(0, 0), (300, 300)]
    assert not validate_model(m, cfg).ok


def test_wrong_namespace_is_error(decisions, cfg):
    m = good(decisions, cfg)
    m.namespaces["crs"] = "http://example.com/wrong"
    assert not validate_model(m, cfg).ok


def test_unknown_parameter_is_warning_not_error(decisions, cfg):
    m = good(decisions, cfg)
    m.attrs["FutureAdobeThing"] = "1"
    rep = validate_model(m, cfg)
    assert rep.ok and any("FutureAdobeThing" in w for w in rep.warnings)


def test_reference_structure_comparison(decisions, cfg):
    m = good(decisions, cfg)
    ref = XMPModel()
    ref.set("Exposure2012", 0)
    ref.attrs["SomethingOnlyInReference"] = "1"
    ref.set_curve("ToneCurvePV2012", [(0, 0), (255, 255)])
    rep = validate_model(m, cfg, ref)
    assert rep.compat["missing_from_generated"] == ["SomethingOnlyInReference"]
    assert 0 < rep.compat["coverage_of_reference"] < 1 and rep.warnings


def test_validate_file_paths(decisions, cfg, tmp_path):
    p = tmp_path / "a.xmp"
    p.write_text(XMPSerializer.serialize(good(decisions, cfg)), encoding="utf-8")
    assert validate_file(p, cfg).ok
    bad = tmp_path / "b.xmp"
    bad.write_text("<x:xmpmeta", encoding="utf-8")
    r = validate_file(bad, cfg)
    assert not r.ok and r.errors
    assert not validate_file(tmp_path / "missing.xmp", cfg).ok
