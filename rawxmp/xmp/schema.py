"""Registry of supported ``crs:*`` parameters (the single source of truth for validation).

Built from the public Camera Raw settings vocabulary as written in Lightroom/ACR sidecars.
It is NOT derived from a user-supplied reference file (none was available when this was
written): use ``rawxmp validate --reference your.xmp`` to diff the generated structure
against your real XMP and extend this table if your file carries more fields.
"""
from __future__ import annotations

from dataclasses import dataclass

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "tiff": "http://ns.adobe.com/tiff/1.0/",
    "exif": "http://ns.adobe.com/exif/1.0/",
    "aux": "http://ns.adobe.com/exif/1.0/aux/",
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "xmpMM": "http://ns.adobe.com/xap/1.0/mm/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "crd": "http://ns.adobe.com/camera-raw-defaults/1.0/",
    "crs": "http://ns.adobe.com/camera-raw-settings/1.0/",
}
CRS_NS = NS["crs"]
REQUIRED_NS = ("x", "rdf", "crs")

COLORS = ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]


@dataclass(frozen=True)
class ParamSpec:
    name: str
    fmt: str                      # int | sint | sf1 | sf2 | str | bool | ibool
    lo: float | None = None
    hi: float | None = None
    default: object = None
    choices: tuple[str, ...] | None = None


def _s(name, fmt, lo=None, hi=None, default=None, choices=None):
    return ParamSpec(name, fmt, lo, hi, default, choices)


_SPECS: list[ParamSpec] = [
    _s("Version", "str", default="16.0"),
    _s("ProcessVersion", "str", default="11.0"),
    _s("WhiteBalance", "str", default="As Shot",
       choices=("As Shot", "Auto", "Daylight", "Cloudy", "Shade", "Tungsten", "Fluorescent", "Flash", "Custom")),
    _s("Temperature", "int", 2000, 50000),
    _s("Tint", "sint", -150, 150),
    _s("Exposure2012", "sf2", -5, 5),
    _s("Contrast2012", "sint", -100, 100),
    _s("Highlights2012", "sint", -100, 100),
    _s("Shadows2012", "sint", -100, 100),
    _s("Whites2012", "sint", -100, 100),
    _s("Blacks2012", "sint", -100, 100),
    _s("Texture", "sint", -100, 100),
    _s("Clarity2012", "sint", -100, 100),
    _s("Dehaze", "sint", -100, 100),
    _s("Vibrance", "sint", -100, 100),
    _s("Saturation", "sint", -100, 100),
    _s("ParametricShadows", "sint", -100, 100),
    _s("ParametricDarks", "sint", -100, 100),
    _s("ParametricLights", "sint", -100, 100),
    _s("ParametricHighlights", "sint", -100, 100),
    _s("ParametricShadowSplit", "int", 10, 70),
    _s("ParametricMidtoneSplit", "int", 20, 80),
    _s("ParametricHighlightSplit", "int", 30, 90),
    _s("Sharpness", "int", 0, 150),
    _s("SharpenRadius", "sf1", 0.5, 3.0),
    _s("SharpenDetail", "int", 0, 100),
    _s("SharpenEdgeMasking", "int", 0, 100),
    _s("LuminanceSmoothing", "int", 0, 100),
    _s("LuminanceNoiseReductionDetail", "int", 0, 100),
    _s("LuminanceNoiseReductionContrast", "int", 0, 100),
    _s("ColorNoiseReduction", "int", 0, 100),
    _s("ColorNoiseReductionDetail", "int", 0, 100),
    _s("ColorNoiseReductionSmoothness", "int", 0, 100),
    _s("ToneCurveName2012", "str", default="Custom", choices=("Linear", "Medium Contrast", "Strong Contrast", "Custom")),
    _s("CameraProfile", "str", default="Adobe Standard"),
    _s("LensProfileEnable", "int", 0, 1),
    _s("LensProfileSetup", "str", choices=("LensDefaults", "Auto", "Custom")),
    _s("LensProfileName", "str"),
    _s("LensProfileFilename", "str"),
    _s("LensProfileDistortionScale", "int", 0, 200),
    _s("LensProfileChromaticAberrationScale", "int", 0, 200),
    _s("LensProfileVignettingScale", "int", 0, 200),
    _s("AutoLateralCA", "int", 0, 1),
    _s("PostCropVignetteAmount", "sint", -100, 100),
    _s("ShadowTint", "sint", -100, 100),
    _s("RedHue", "sint", -100, 100), _s("RedSaturation", "sint", -100, 100),
    _s("GreenHue", "sint", -100, 100), _s("GreenSaturation", "sint", -100, 100),
    _s("BlueHue", "sint", -100, 100), _s("BlueSaturation", "sint", -100, 100),
    _s("SplitToningShadowHue", "int", 0, 360), _s("SplitToningShadowSaturation", "int", 0, 100),
    _s("SplitToningHighlightHue", "int", 0, 360), _s("SplitToningHighlightSaturation", "int", 0, 100),
    _s("SplitToningBalance", "sint", -100, 100),
    _s("ColorGradeMidtoneHue", "int", 0, 360), _s("ColorGradeMidtoneSat", "int", 0, 100),
    _s("ColorGradeShadowLum", "sint", -100, 100), _s("ColorGradeMidtoneLum", "sint", -100, 100),
    _s("ColorGradeHighlightLum", "sint", -100, 100),
    _s("ColorGradeGlobalHue", "int", 0, 360), _s("ColorGradeGlobalSat", "int", 0, 100),
    _s("ColorGradeGlobalLum", "sint", -100, 100),
    _s("ColorGradeBlending", "int", 0, 100),
    _s("ConvertToGrayscale", "bool"),
    _s("HasSettings", "bool"),
    _s("AlreadyApplied", "bool"),
    _s("RawFileName", "str"),
    # fields seen in real Lightroom/Bridge sidecars (kept so templates validate cleanly)
    _s("HasCrop", "bool"), _s("CropTop", "f", 0, 1), _s("CropLeft", "f", 0, 1), _s("CropBottom", "f", 0, 1),
    _s("CropRight", "f", 0, 1), _s("CropAngle", "f", -45, 45), _s("CropConstrainToWarp", "int", 0, 1),
    _s("ToneCurveName", "str"), _s("CameraProfileDigest", "str"), _s("about", "str"),
    _s("OverrideLookVignette", "bool"), _s("VignetteAmount", "sint", -100, 100), _s("VignetteMidpoint", "int", 0, 100),
    _s("GrainAmount", "int", 0, 100), _s("GrainSize", "int", 0, 100), _s("GrainFrequency", "int", 0, 100),
    _s("LensManualDistortionAmount", "sint", -100, 100),
    _s("PerspectiveVertical", "sint", -100, 100), _s("PerspectiveHorizontal", "sint", -100, 100),
    _s("PerspectiveRotate", "f", -10, 10), _s("PerspectiveScale", "int", 50, 150), _s("PerspectiveAspect", "sint", -100, 100),
    _s("PerspectiveX", "f"), _s("PerspectiveY", "f"),
    _s("DefringePurpleAmount", "int", 0, 20), _s("DefringePurpleHueLo", "int", 0, 100), _s("DefringePurpleHueHi", "int", 0, 100),
    _s("DefringeGreenAmount", "int", 0, 20), _s("DefringeGreenHueLo", "int", 0, 100), _s("DefringeGreenHueHi", "int", 0, 100),
]
for _c in COLORS:
    _SPECS += [_s(f"GrayMixer{_c}", "sint", -100, 100),
               _s(f"HueAdjustment{_c}", "sint", -100, 100),
               _s(f"SaturationAdjustment{_c}", "sint", -100, 100),
               _s(f"LuminanceAdjustment{_c}", "sint", -100, 100)]

SPECS: dict[str, ParamSpec] = {s.name: s for s in _SPECS}
ORDER: list[str] = [s.name for s in _SPECS]

CURVES = ("ToneCurve", "ToneCurveRed", "ToneCurveGreen", "ToneCurveBlue",
          "ToneCurvePV2012", "ToneCurvePV2012Red", "ToneCurvePV2012Green", "ToneCurvePV2012Blue")


# Per-image values that must never leak from a template into another photo's sidecar
PER_IMAGE = ("Temperature", "Tint")


def format_value(spec: ParamSpec, v) -> str:
    """Format as Camera Raw writes it (signed integers, 2-decimal exposure, True/False)."""
    f = spec.fmt
    if f == "str":
        return str(v)
    if f == "bool":
        return "True" if (v is True or str(v).lower() in ("true", "1")) else "False"
    x = float(v)
    if f == "int":
        return str(int(round(x)))
    if f == "sint":
        n = int(round(x))
        return f"{n:+d}" if n else "0"
    if f == "sf2":
        return f"{x:+.2f}" if abs(x) >= 0.005 else "0.00"
    if f == "sf1":
        return f"{x:+.1f}"
    if f == "f":
        return f"{x:g}"
    raise ValueError(f"unknown format {f}")


def parse_value(spec: ParamSpec, text: str):
    """Inverse of format_value; raises ValueError for malformed numbers."""
    if spec.fmt == "str":
        return text
    if spec.fmt == "bool":
        if text not in ("True", "False"):
            raise ValueError(f"booleano inválido: {text!r}")
        return text == "True"
    return float(text)
