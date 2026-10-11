import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from rawxmp.config import load_config
from rawxmp.xmp.builder import build_model
from rawxmp.xmp.model import XMPModel, XMPModelError
from rawxmp.xmp.parser import parse_xmp
from rawxmp.xmp.schema import CRS_NS, SPECS, format_value
from rawxmp.xmp.serializer import XMPSerializer
from rawxmp.xmp.parser import parse_xmp as _p

CRS = "{" + CRS_NS + "}"


def test_number_formatting_matches_camera_raw_style():
    f = lambda n, v: format_value(SPECS[n], v)
    assert f("Exposure2012", 0.35) == "+0.35" and f("Exposure2012", -1) == "-1.00" and f("Exposure2012", 0) == "0.00"
    assert f("Tint", 8) == "+8" and f("Tint", -3.4) == "-3" and f("Tint", 0) == "0"
    assert f("Temperature", 5200.4) == "5200"
    assert f("SharpenRadius", 1.0) == "+1.0"
    assert f("HasSettings", True) == "True" and f("ConvertToGrayscale", False) == "False"


def test_serialised_xml_is_valid_and_has_expected_structure():
    m = XMPModel()
    m.set("Exposure2012", 0.35)
    m.set("Tint", 8)
    m.set_curve("ToneCurvePV2012", [(0, 0), (128, 130), (255, 255)])
    text = XMPSerializer.serialize(m)
    assert text.startswith("<?xpacket begin=") and text.rstrip().endswith("<?xpacket end=\"w\"?>")
    body = text[text.index("<x:xmpmeta"): text.index("</x:xmpmeta>") + len("</x:xmpmeta>")]
    root = ET.fromstring(body)
    assert root.tag == "{adobe:ns:meta/}xmpmeta"
    desc = root.find(".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description")
    assert desc.attrib[CRS + "Exposure2012"] == "+0.35"
    lis = [li.text for li in desc.iter("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li")]
    assert lis == ["0, 0", "128, 130", "255, 255"]


def test_special_characters_are_escaped():
    m = XMPModel()
    m.set("CameraProfile", 'Adobe "Std" & <Neutral>')
    text = XMPSerializer.serialize(m)
    ET.fromstring(text[text.index("<x:xmpmeta"): text.index("</x:xmpmeta>") + 12])       # must parse
    assert parse_xmp(text).get("CameraProfile") == 'Adobe "Std" & <Neutral>'


def test_roundtrip_preserves_everything(decisions, cfg):
    d = decisions["normal"]
    m = build_model(d.params, d.curves, cfg)
    again = parse_xmp(XMPSerializer.serialize(m))
    assert dict(again.attrs) == dict(m.attrs)
    assert dict(again.curves) == dict(m.curves)


def test_unknown_parameter_and_bad_values_rejected():
    m = XMPModel()
    with pytest.raises(XMPModelError):
        m.set("NotARealParameter", 1)
    with pytest.raises(XMPModelError):
        m.set("Exposure2012", "abc")
    with pytest.raises(XMPModelError):
        m.set_curve("NoSuchCurve", [(0, 0)])


def test_no_duplicate_attributes_in_output(decisions, cfg):
    d = decisions["normal"]
    text = XMPSerializer.serialize(build_model(d.params, d.curves, cfg))
    ET.fromstring(text[text.index("<x:xmpmeta"): text.index("</x:xmpmeta>") + 12])   # dup attrs would raise
    import re
    names = re.findall(r"\s(crs:\w+)=", text)
    assert len(names) == len(set(names))


def test_template_fields_are_preserved_and_overridden(decisions, cfg):
    tpl = XMPModel()
    tpl.set("Exposure2012", 3.0)
    tpl.attrs["CustomVendorField"] = "keep-me"
    tpl.other_attrs["xmp:Rating"] = "4"
    tpl.extra_xml.append('<crs:Look xmlns:crs="%s"><crs:Name>My Look</crs:Name></crs:Look>' % CRS_NS)
    d = decisions["normal"]
    m = build_model(d.params, d.curves, cfg, tpl)
    assert m.attrs["Exposure2012"] == "%+.2f" % d.params["Exposure2012"]      # overridden
    assert m.attrs["CustomVendorField"] == "keep-me" and m.other_attrs["xmp:Rating"] == "4"
    again = parse_xmp(XMPSerializer.serialize(m))
    assert again.attrs["CustomVendorField"] == "keep-me" and again.other_attrs["xmp:Rating"] == "4"
    assert any("My Look" in x for x in again.extra_xml)
    assert tpl.attrs["Exposure2012"] == "+3.00"                               # template itself untouched


def test_malformed_xml_rejected(tmp_path):
    with pytest.raises(XMPModelError):
        parse_xmp("<x:xmpmeta><unclosed>")
    p = tmp_path / "a.xmp"
    p.write_text("not xml at all", encoding="utf-8")
    with pytest.raises(XMPModelError):
        parse_xmp(p)


def test_generated_xmp_has_all_expected_sections(decisions, cfg):
    d = decisions["warm_cast"]
    m = build_model(d.params, d.curves, cfg)
    for k in ("Temperature", "Tint", "WhiteBalance", "Exposure2012", "Contrast2012", "Highlights2012",
              "Shadows2012", "Whites2012", "Blacks2012", "Vibrance", "Saturation", "CameraProfile",
              "Sharpness", "LuminanceSmoothing", "ColorNoiseReduction", "LensProfileEnable",
              "ParametricShadows", "RedHue", "HueAdjustmentRed", "LuminanceAdjustmentMagenta"):
        assert k in m.attrs, k
    assert {"ToneCurve", "ToneCurvePV2012", "ToneCurvePV2012Red"} <= set(m.curves)


# ---- real Lightroom/Bridge sidecars supplied by the user (tests/data) ------------------------------
REF = Path(__file__).parent / "data"


def test_real_reference_xmp_is_parsed_in_element_form():
    m = parse_xmp(REF / "ref_a.xmp")
    assert m.form == "elements" and len(m.attrs) > 100
    assert m.attrs["Exposure2012"] == "2.17" and m.attrs["Temperature"] == "5910"
    assert m.curves["ToneCurve"][1] == (32, 22) and m.curves["ToneCurvePV2012"] == [(0, 0), (255, 255)]
    assert any("Look" in x for x in m.extra_xml)                      # nested Look block preserved verbatim
    assert m.other_attrs.get("xmp:Rating") == "0"


def test_real_reference_roundtrips_without_losing_anything():
    m = parse_xmp(REF / "ref_a.xmp")
    again = parse_xmp(XMPSerializer.serialize(m))
    assert dict(again.attrs) == dict(m.attrs) and dict(again.curves) == dict(m.curves)
    assert again.form == "elements" and len(again.extra_xml) == len(m.extra_xml)


def test_real_reference_validates_and_empty_file_does_not():
    from rawxmp.validation.xmp_validator import validate_model
    assert validate_model(parse_xmp(REF / "ref_a.xmp"), None).ok
    empty = XMPModel()
    assert not validate_model(empty, None).ok          # regression: "OK" with zero parameters


def test_template_never_leaks_per_image_white_balance(decisions, cfg):
    tpl = parse_xmp(REF / "ref_a.xmp")
    d = decisions["normal"]                             # As Shot → engine writes no Temperature/Tint
    m = build_model(d.params, d.curves, cfg, tpl)
    assert "Temperature" not in m.attrs and "Tint" not in m.attrs
    assert m.attrs["WhiteBalance"] == "As Shot" and m.attrs["Version"] == '"15.4"'   # template's own version kept
    assert m.form == "elements" and m.attrs["HasCrop"] == "True"                  # structure of the user's file kept
