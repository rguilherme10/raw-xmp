"""Derive a STYLE profile (profiles/styles/<name>.yaml) from one or more human-made XMPs.

Rule of thumb applied here:
  * STYLE  = what you apply the same way to every photo (curve, HSL, parametric, detail, vignette, camera profile,
             and the *typical level* of Contrast/Highlights/Shadows/Whites/Blacks);
  * IMAGE CORRECTION = Exposure and white balance (Temperature/Tint) — never copied, always computed per photo.
The tonal sliders become additive biases: final = (what the engine measures for that photo) + your usual level.
"""
from __future__ import annotations

import statistics
from pathlib import Path

import numpy as np
import yaml

from .xmp.model import XMPModel
from .xmp.parser import parse_xmp
from .xmp.schema import COLORS

TONAL = {"Contrast2012": "contrast_bias", "Highlights2012": "highlights_bias", "Shadows2012": "shadows_bias",
         "Whites2012": "whites_bias", "Blacks2012": "blacks_bias"}
PER_IMAGE_REPORT = ("Exposure2012", "Temperature", "Tint", "WhiteBalance")
GRID = [0, 32, 64, 128, 192, 255]


def _vals(models: list[XMPModel], key: str) -> list[float]:
    return [v for m in models if (v := m.get_float(key)) is not None]


def _med(models, key, default=None, as_int=True):
    v = _vals(models, key)
    if not v:
        return default
    x = statistics.median(v)
    return int(round(x)) if as_int else round(x, 2)


def _spread(models, key) -> float:
    v = _vals(models, key)
    return (max(v) - min(v)) if len(v) > 1 else 0.0


def _curve_on_grid(points: list[tuple[int, int]]) -> list[list[int]]:
    xs, ys = zip(*points)
    return [[x, int(round(float(np.interp(x, xs, ys))))] for x in GRID]


def build_style(models: list[XMPModel], name: str) -> tuple[dict, list[str]]:
    notes: list[str] = []
    st: dict = {"name": name}
    first = models[0]
    st["camera_profile"] = first.get("CameraProfile", "Adobe Standard")
    for key, out in (("Saturation", "saturation"), ("Vibrance", "vibrance"), ("Clarity2012", "clarity"),
                     ("Texture", "texture"), ("Dehaze", "dehaze")):
        v = _med(models, key)
        if v is not None:
            st[out] = v
    for key, out in TONAL.items():
        v = _med(models, key)
        if v is not None:
            st[out] = v
            sp = _spread(models, key)
            notes.append(f"{key}: mediana {v:+d} (variação entre fotos: {sp:.0f})"
                         + ("  ← varia bastante; considere se é estilo ou correção" if sp > 15 else ""))
    # tone curve: PV2012 (effective) on the engine's grid; legacy ToneCurve kept verbatim
    pv = first.curves.get("ToneCurvePV2012")
    legacy = first.curves.get("ToneCurve")
    tc: dict = {}
    if pv:
        tc["points"] = _curve_on_grid(pv)
    if legacy and legacy != pv:
        tc["legacy_points"] = [list(p) for p in legacy]
        if pv == [(0, 0), (255, 255)]:
            notes.append("A curva de pontos ToneCurve (legado) é ignorada pelo Camera Raw no processo 2012; "
                         "a ToneCurvePV2012 é linear. O 'estilo' efetivo da curva vem do bloco Look "
                         "(use --with-template para preservá-lo).")
    tc["parametric"] = {"shadows": _med(models, "ParametricShadows", 0), "darks": _med(models, "ParametricDarks", 0),
                        "lights": _med(models, "ParametricLights", 0), "highlights": _med(models, "ParametricHighlights", 0)}
    st["tone_curve"] = tc
    hsl = {"hue": {}, "saturation": {}, "luminance": {}}
    for pre, grp in (("Hue", "hue"), ("Saturation", "saturation"), ("Luminance", "luminance")):
        for c in COLORS:
            v = _med(models, f"{pre}Adjustment{c}")
            if v is not None:
                hsl[grp][c.lower()] = v
    st["hsl"] = hsl
    sh = {k: _med(models, x, d, as_int=(k != "radius")) for k, x, d in
          (("amount", "Sharpness", 40), ("radius", "SharpenRadius", 1.0), ("detail", "SharpenDetail", 25),
           ("masking", "SharpenEdgeMasking", 0))}
    st["sharpening"] = sh
    st["noise_reduction"] = {
        "luminance": _med(models, "LuminanceSmoothing", 0), "luminance_detail": _med(models, "LuminanceNoiseReductionDetail", 50),
        "luminance_contrast": _med(models, "LuminanceNoiseReductionContrast", 0), "color": _med(models, "ColorNoiseReduction", 25),
        "color_detail": _med(models, "ColorNoiseReductionDetail", 50), "color_smoothness": _med(models, "ColorNoiseReductionSmoothness", 50)}
    st["color_grade"] = {
        "shadows": {"hue": _med(models, "SplitToningShadowHue", 0), "sat": _med(models, "SplitToningShadowSaturation", 0),
                    "lum": _med(models, "ColorGradeShadowLum", 0)},
        "midtones": {"hue": _med(models, "ColorGradeMidtoneHue", 0), "sat": _med(models, "ColorGradeMidtoneSat", 0),
                     "lum": _med(models, "ColorGradeMidtoneLum", 0)},
        "highlights": {"hue": _med(models, "SplitToningHighlightHue", 0), "sat": _med(models, "SplitToningHighlightSaturation", 0),
                       "lum": _med(models, "ColorGradeHighlightLum", 0)},
        "global": {"hue": _med(models, "ColorGradeGlobalHue", 0), "sat": _med(models, "ColorGradeGlobalSat", 0),
                   "lum": _med(models, "ColorGradeGlobalLum", 0)},
        "blending": _med(models, "ColorGradeBlending", 50), "balance": _med(models, "SplitToningBalance", 0)}
    st["calibration"] = {"shadow_tint": _med(models, "ShadowTint", 0), "red_hue": _med(models, "RedHue", 0),
                         "red_saturation": _med(models, "RedSaturation", 0), "green_hue": _med(models, "GreenHue", 0),
                         "green_saturation": _med(models, "GreenSaturation", 0), "blue_hue": _med(models, "BlueHue", 0),
                         "blue_saturation": _med(models, "BlueSaturation", 0)}
    st["vignette"] = _med(models, "PostCropVignetteAmount", 0)
    for k in PER_IMAGE_REPORT:
        vals = _vals(models, k)
        if vals:
            notes.append(f"{k}: NÃO copiado (correção por foto). Nas suas referências: "
                         f"{min(vals):g} … {max(vals):g}")
    return {"style": st}, notes


def write_style(models_paths: list[str | Path], name: str, out_dir: Path, with_template: bool = False,
                force: bool = False) -> tuple[Path, list[str]]:
    import re
    import shutil
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise ValueError("nome de estilo inválido (use letras, números, _ e -)")
    models = [parse_xmp(p) for p in models_paths]
    doc, notes = build_style(models, name)
    dest = out_dir / "styles" / f"{name}.yaml"
    if dest.exists() and not force:
        raise FileExistsError(f"{dest} já existe (use --force)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if with_template:
        tdir = out_dir / "templates"
        tdir.mkdir(parents=True, exist_ok=True)
        tpath = tdir / f"{name}.xmp"
        shutil.copyfile(models_paths[0], tpath)
        doc["style"]["template"] = f"profiles/templates/{name}.xmp"
        notes.append(f"Template estrutural copiado para {tpath} (Look, crop, formato de elementos preservados; "
                     "Temperature/Tint nunca são herdados).")
    header = (f"# Estilo '{name}' gerado de {len(models)} XMP(s) por `rawxmp make-style`.\n"
              "# Edite livremente. Exposição e balanço de branco NÃO fazem parte do estilo (são calculados por foto).\n")
    dest.write_text(header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return dest, notes
