from pathlib import Path

import pytest
import yaml

from rawxmp.cli.main import main
from rawxmp.config import Config, ROOT, load_config
from rawxmp.pipeline.batch import Options, run_batch
from rawxmp.style_builder import build_style, write_style
from rawxmp.validation.xmp_validator import validate_file
from rawxmp.xmp.parser import parse_xmp

REF = [Path(__file__).parent / "data" / f"ref_{c}.xmp" for c in "abc"]


def test_style_extracts_constant_look_and_skips_per_image_values():
    doc, notes = build_style([parse_xmp(p) for p in REF], "t")
    st = doc["style"]
    assert st["vibrance"] == 11 and st["saturation"] == -4
    assert st["highlights_bias"] == -43 and st["contrast_bias"] == 27 and st["shadows_bias"] == 34
    assert st["blacks_bias"] == -14 and st["whites_bias"] == 0
    assert st["hsl"]["saturation"]["green"] == -11 and st["hsl"]["hue"]["green"] == 11
    assert st["hsl"]["luminance"]["orange"] == 8
    assert st["sharpening"] == {"amount": 67, "radius": 1.0, "detail": 25, "masking": 42}
    assert st["noise_reduction"]["luminance"] == 18 and st["vignette"] == -8
    assert st["tone_curve"]["parametric"] == {"shadows": -15, "darks": 20, "lights": 0, "highlights": 0}
    assert st["tone_curve"]["points"] == [[0, 0], [32, 32], [64, 64], [128, 128], [192, 192], [255, 255]]
    assert st["tone_curve"]["legacy_points"][1] == [32, 22]
    flat = yaml.safe_dump(doc)
    assert "Exposure2012" not in flat and "Temperature" not in flat           # never part of a style
    assert any("NÃO copiado" in n and "Exposure2012" in n for n in notes)


def test_written_style_loads_and_is_used(tmp_path, raw_set):
    prof = tmp_path / "profiles"
    dest, _ = write_style(REF, "ref-test", prof, with_template=True)
    assert dest.exists() and (prof / "templates" / "ref-test.xmp").exists()
    with pytest.raises(FileExistsError):
        write_style(REF, "ref-test", prof)
    write_style(REF, "ref-test", prof, force=True)
    with pytest.raises(ValueError):
        write_style(REF, "../evil", prof)
    # the YAML merges over the base style through the normal loader
    import rawxmp.config as C
    data = yaml.safe_load(dest.read_text(encoding="utf-8"))
    cfg = Config(C._merge(load_config().data, data))
    from rawxmp.correction.engine import analyze_raw
    d = analyze_raw(raw_set["normal"], cfg)
    assert d.params["Vibrance"] >= 11 - 6 and d.params["Sharpness"] == 67      # ISO 200: no sharpness drop
    assert d.params["ParametricDarks"] == 20 and d.params["PostCropVignetteAmount"] == -8
    assert d.curves["ToneCurve"][1] == (32, 22)                           # legacy curve kept verbatim
    assert all(abs(y - x) <= 4 for x, y in d.curves["ToneCurvePV2012"])   # effective curve stays near-linear


def test_style_bias_is_added_but_curve_not_double_counted(raw_set, tmp_path):
    base = load_config()
    prof = tmp_path / "p"
    dest, _ = write_style(REF, "r", prof)
    import rawxmp.config as C
    cfg = Config(C._merge(base.data, yaml.safe_load(dest.read_text(encoding="utf-8"))))
    from rawxmp.correction.engine import analyze_raw
    a, b = analyze_raw(raw_set["normal"], base), analyze_raw(raw_set["normal"], cfg)
    assert b.params["Highlights2012"] <= a.params["Highlights2012"] - 30            # your -43 level applied
    assert b.params["Contrast2012"] >= a.params["Contrast2012"] + 20
    # PV2012 curve stays (nearly) linear: your contrast comes from the slider, not from an extra S-curve
    assert all(abs(y - x) <= 4 for x, y in b.curves["ToneCurvePV2012"])


def test_process_with_template_matches_user_format(tmp_path, raw_set):
    import shutil
    d = tmp_path / "ens"
    d.mkdir()
    shutil.copy(raw_set["warm_cast"], d / "A.CR2")
    shutil.copy(raw_set["normal"], d / "B.CR2")
    res = run_batch(Options(path=str(d), workers=1, template=str(REF[0]), write_reports=False))
    assert all(r.status == "written" for r in res)
    text = (d / "B.xmp").read_text(encoding="utf-8")
    assert "<crs:Exposure2012>" in text and "crs:Exposure2012=" not in text      # element form like the user's files
    assert "<crs:Look>" in text or "Look" in text
    assert "<crs:Temperature>" not in text                                      # As Shot: not inherited from the template
    assert "<crs:Temperature>" in (d / "A.xmp").read_text(encoding="utf-8")      # Custom: computed value
    assert validate_file(d / "B.xmp", load_config(), REF[0], enforce_limits=False).ok
    cov = validate_file(d / "B.xmp", load_config(), REF[0], enforce_limits=False).compat["coverage_of_reference"]
    assert cov > 0.9


def test_cli_make_style(tmp_path, capsys):
    prof = tmp_path / "pp"
    assert main(["make-style", *map(str, REF), "--name", "cli-test", "--profiles-dir", str(prof), "-q"]) == 0
    assert (prof / "styles" / "cli-test.yaml").exists()
    assert main(["make-style", *map(str, REF), "--name", "cli-test", "--profiles-dir", str(prof), "-q"]) == 1   # no --force


def test_element_form_numbers_are_unsigned_like_the_users_files(tmp_path, raw_set):
    import shutil
    d = tmp_path / "x"
    d.mkdir()
    shutil.copy(raw_set["normal"], d / "B.CR2")
    run_batch(Options(path=str(d), workers=1, template=str(REF[0]), write_reports=False, style="portrait"))
    text = (d / "B.xmp").read_text(encoding="utf-8")
    import re
    assert re.search(r"<crs:Vibrance>-?\d+</crs:Vibrance>", text)
    assert not re.search(r"<crs:\w+>\+\d", text)          # element form is unsigned, like the user's files


def _xmp_with(tmp_path, name, **vals):
    from rawxmp.xmp.model import XMPModel
    from rawxmp.xmp.serializer import XMPSerializer
    m = XMPModel()
    for k, v in vals.items():
        m.set(k, v)
    p = tmp_path / name
    p.write_text(XMPSerializer.serialize(m), encoding="utf-8")
    return p


def test_style_widens_global_limits_when_they_would_clip_the_users_look(tmp_path):
    paths = [_xmp_with(tmp_path, f"{i}.xmp", Blacks2012=b, Whites2012=w, Highlights2012=-34)
             for i, (b, w) in enumerate([(-61, -24), (-75, -30), (-91, -55)])]
    doc, notes = build_style([parse_xmp(p) for p in paths], "t")
    assert doc["limits"]["blacks"]["min"] <= -100 + 9 and doc["limits"]["whites"]["min"] <= -65
    assert "highlights" not in doc["limits"]                                   # inside the default range: untouched
    assert any("limits.blacks" in n for n in notes)


def test_force_regeneration_keeps_calibrated_sections(tmp_path):
    import yaml
    paths = [_xmp_with(tmp_path, f"{i}.xmp", Blacks2012=-70, Vibrance=10) for i in range(3)]
    prof = tmp_path / "prof"
    dest, _ = write_style(paths, "sess", prof)
    doc = yaml.safe_load(dest.read_text(encoding="utf-8"))
    doc["exposure"] = {"skin_target_y": 0.16, "estimate_gain": {"contrast": 0.0}}          # as written by `calibrate --write-style`
    doc["consistency"] = {"exposure": {"enabled": True}}
    dest.write_text(yaml.safe_dump(doc), encoding="utf-8")
    write_style(paths, "sess", prof, force=True)
    again = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert again["exposure"] == {"skin_target_y": 0.16, "estimate_gain": {"contrast": 0.0}}
    assert again["consistency"] == {"exposure": {"enabled": True}} and again["style"]["vibrance"] == 10
