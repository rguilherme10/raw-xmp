"""Orchestrates the analysis modules and merges STYLE (constant look) with IMAGE CORRECTION."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..analysis.stats import ImageStats, compute_stats
from ..color.hsl import BANDS, HSLResult, analyze_bands, estimate_hsl
from ..colorspace import linear_to_lab, srgb_encode
from ..config import Config
from ..detail.detail import estimate_detail
from ..exposure.estimator import estimate_exposure
from ..lens.lens import lens_settings
from ..raw.reader import RawInfo, RawReader
from ..raw.render import render_for_analysis
from ..skin.detector import SkinResult, detect_skin
from ..tone.curve import CurveResult, build_curve
from ..types import Estimate, clamp, clip01
from ..white_balance.estimator import WBResult, estimate_white_balance

HSL_PREFIX = {"hue": "HueAdjustment", "saturation": "SaturationAdjustment", "luminance": "LuminanceAdjustment"}


@dataclass
class Decision:
    """Everything needed to write the XMP and the audit report."""
    info: RawInfo
    stats: ImageStats
    skin: SkinResult
    wb: WBResult
    hsl: HSLResult
    curve: CurveResult
    estimates: dict[str, Estimate]
    params: dict                       # crs:* name → raw value (pre-format)
    curves: dict
    clamps: list[str] = field(default_factory=list)
    style: str = "base"
    camera_profile: str | None = None
    timings: dict = field(default_factory=dict)

    # --- audit report -------------------------------------------------------------------
    def to_report(self, cfg: Config | None = None, include_hist: bool = True) -> dict:
        stats = self.stats.to_dict()
        if not include_hist:
            stats.pop("histogram_v", None)
        return {
            "file": self.info.file_name,
            "style": self.style,
            "camera_rule_set": self.camera_profile,
            "metadata": self.info.to_dict(),
            "exposure_analysis": stats,
            "white_balance": self.wb.to_dict(),
            "clipping": {
                "sensor_clip_fraction": self.stats.sensor_clip_fraction,
                "render_any_channel": self.stats.clip_fraction_any_channel,
                "per_channel": self.stats.clip_fraction_channel,
                "near_black_fraction": self.stats.near_black_fraction,
            },
            "colors": self.hsl.to_dict(),
            "skin": self.skin.to_dict(),
            "tone_curve": self.curve.to_dict(),
            "estimates": {k: v.to_dict() for k, v in self.estimates.items()},
            "parameters": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.params.items()},
            "clamped": self.clamps,
            "timings_s": {k: round(v, 3) for k, v in self.timings.items()},
        }


def _limit(cfg: Config, key: str, value: float, name: str, clamps: list[str]) -> float:
    lo, hi = cfg.limit(key)
    v = clamp(value, lo, hi)
    if v != value:
        clamps.append(f"{name}: {value:.2f} → {v:.2f} (limite {lo}..{hi})")
    return v


def analyze_raw(path: str | Path, cfg: Config, cache_dir: str | Path | None = None) -> Decision:
    t0 = time.perf_counter()
    reader = RawReader(path)
    info = reader.read_info()
    cfg = cfg.with_camera(info.camera)
    lin = render_for_analysis(reader, cfg.get("analysis.max_dimension", 1024), cache_dir or cfg.get("analysis.cache_dir"))
    t1 = time.perf_counter()

    skin = detect_skin(lin, cfg)
    wb = estimate_white_balance(lin, info, cfg, skin)
    lin_wb = np.clip(lin @ wb.matrix.T.astype(np.float32), 0, 1) if wb.mode == "Custom" else lin
    stats = compute_stats(lin_wb, cfg, info.raw_clip_fraction)
    est = estimate_exposure(stats, skin, cfg, style_bias=float(cfg.get("style.exposure_bias", 0.0)))

    # colour analysis on the image as it will look after exposure (simulated), to judge saturation/clipping
    sim = np.clip(lin_wb * (2.0 ** est["exposure"].value), 0, 1)
    bands = analyze_bands(sim, cfg)
    hsl = estimate_hsl(bands, skin, cfg)
    curve = build_curve(cfg, est, stats, skin)
    det = estimate_detail(info.iso, cfg)
    lens_params, lens_est = lens_settings(info.lens, cfg)
    est.update({f"detail_{k}": v for k, v in det.items()})
    est["lens"] = lens_est

    # global saturation / vibrance: style baseline + mild image-driven nudge from mean chroma
    lab = linear_to_lab(sim)
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    mean_c = float(chroma.mean())
    vib_adj, sat_adj, vwhy = 0.0, 0.0, f"croma médio C*={mean_c:.1f}: sem ajuste"
    if mean_c < 9:
        vib_adj, vwhy = 6.0, f"croma médio baixo (C*={mean_c:.1f}): vibrance +6"
    elif mean_c > 42:
        vib_adj, sat_adj, vwhy = -6.0, -3.0, f"croma médio alto (C*={mean_c:.1f}): vibrance −6, saturação −3"
    est["vibrance"] = Estimate(vib_adj, 0.6, vwhy)
    est["saturation"] = Estimate(sat_adj, 0.6, vwhy)
    est["white_balance_mode"] = Estimate(wb.mode, wb.temperature.confidence, wb.temperature.reason)
    t2 = time.perf_counter()

    clamps: list[str] = []
    P: dict = {}
    st = cfg.section("style")
    P["Version"] = cfg.get("xmp.crs_version")
    P["ProcessVersion"] = cfg.get("xmp.process_version")
    P["WhiteBalance"] = wb.mode
    if wb.mode == "Custom" or cfg.get("white_balance.write_as_shot_values", False):
        P["Temperature"] = round(_limit(cfg, "temperature", wb.temperature.value, "Temperature", clamps))
        P["Tint"] = round(_limit(cfg, "tint", wb.tint.value, "Tint", clamps))
    P["Exposure2012"] = _limit(cfg, "exposure", est["exposure"].value, "Exposure", clamps)
    P["Contrast2012"] = _limit(cfg, "contrast", est["contrast"].value, "Contrast", clamps)
    P["Highlights2012"] = _limit(cfg, "highlights", est["highlights"].value, "Highlights", clamps)
    P["Shadows2012"] = _limit(cfg, "shadows", est["shadows"].value, "Shadows", clamps)
    P["Whites2012"] = _limit(cfg, "whites", est["whites"].value, "Whites", clamps)
    P["Blacks2012"] = _limit(cfg, "blacks", est["blacks"].value, "Blacks", clamps)
    P["Clarity2012"] = _limit(cfg, "clarity", st["clarity"], "Clarity", clamps)
    P["Texture"] = _limit(cfg, "texture", st["texture"], "Texture", clamps)
    P["Dehaze"] = _limit(cfg, "dehaze", st["dehaze"], "Dehaze", clamps)
    P["Vibrance"] = _limit(cfg, "vibrance", st["vibrance"] + vib_adj, "Vibrance", clamps)
    P["Saturation"] = _limit(cfg, "saturation", st["saturation"] + sat_adj, "Saturation", clamps)
    par = st["tone_curve"]["parametric"]
    P.update(ParametricShadows=par["shadows"], ParametricDarks=par["darks"], ParametricLights=par["lights"],
             ParametricHighlights=par["highlights"], ParametricShadowSplit=25, ParametricMidtoneSplit=50,
             ParametricHighlightSplit=75)
    P["ToneCurveName2012"] = "Custom"
    P["CameraProfile"] = st["camera_profile"]
    # HSL = style baseline + evidence-driven correction
    for band in BANDS:
        cname = band.capitalize()
        for key, lim, res in (("hue", "hsl_hue", hsl.hue), ("saturation", "hsl_saturation", hsl.saturation),
                              ("luminance", "hsl_luminance", hsl.luminance)):
            base = st["hsl"][key].get(band, 0)
            corr = res[band].value if band in res else 0
            P[f"{HSL_PREFIX[key]}{cname}"] = round(_limit(cfg, lim, base + corr, f"HSL {key} {cname}", clamps))
    sh = st["sharpening"]
    P["Sharpness"] = det["sharpness"].value
    P["SharpenRadius"] = sh["radius"]
    P["SharpenDetail"] = sh["detail"]
    P["SharpenEdgeMasking"] = det["masking"].value
    nr = st["noise_reduction"]
    P["LuminanceSmoothing"] = det["luminance_nr"].value
    P["LuminanceNoiseReductionDetail"] = nr["luminance_detail"]
    P["LuminanceNoiseReductionContrast"] = nr["luminance_contrast"]
    P["ColorNoiseReduction"] = det["color_nr"].value
    P["ColorNoiseReductionDetail"] = nr["color_detail"]
    P["ColorNoiseReductionSmoothness"] = nr["color_smoothness"]
    P.update(lens_params)
    cal = st["calibration"]
    P.update(ShadowTint=cal["shadow_tint"], RedHue=cal["red_hue"], RedSaturation=cal["red_saturation"],
             GreenHue=cal["green_hue"], GreenSaturation=cal["green_saturation"],
             BlueHue=cal["blue_hue"], BlueSaturation=cal["blue_saturation"])
    cg = st["color_grade"]
    P.update(SplitToningShadowHue=cg["shadows"]["hue"], SplitToningShadowSaturation=cg["shadows"]["sat"],
             SplitToningHighlightHue=cg["highlights"]["hue"], SplitToningHighlightSaturation=cg["highlights"]["sat"],
             SplitToningBalance=cg["balance"], ColorGradeBlending=cg["blending"],
             ColorGradeShadowLum=cg["shadows"]["lum"], ColorGradeMidtoneHue=cg["midtones"]["hue"],
             ColorGradeMidtoneSat=cg["midtones"]["sat"], ColorGradeMidtoneLum=cg["midtones"]["lum"],
             ColorGradeHighlightLum=cg["highlights"]["lum"], ColorGradeGlobalHue=cg["global"]["hue"],
             ColorGradeGlobalSat=cg["global"]["sat"], ColorGradeGlobalLum=cg["global"]["lum"])
    P["PostCropVignetteAmount"] = st["vignette"]
    P["ConvertToGrayscale"] = False
    P["HasSettings"] = True
    P["AlreadyApplied"] = False
    P["RawFileName"] = info.file_name

    curves = {"ToneCurvePV2012": curve.points, "ToneCurve": curve.points,
              "ToneCurvePV2012Red": [(0, 0), (255, 255)], "ToneCurvePV2012Green": [(0, 0), (255, 255)],
              "ToneCurvePV2012Blue": [(0, 0), (255, 255)]}
    t3 = time.perf_counter()
    return Decision(info=info, stats=stats, skin=skin, wb=wb, hsl=hsl, curve=curve, estimates=est, params=P,
                    curves=curves, clamps=clamps, style=cfg.style_name, camera_profile=cfg.camera_profile,
                    timings={"read_render": t1 - t0, "analysis": t2 - t1, "assemble": t3 - t2})
