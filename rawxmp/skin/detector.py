"""Skin-tone detection and analysis (colour + geometry; optional Haar face check).

Colour-only skin detection can be fooled by wood, sand, cardboard: that is why the
confidence is capped (skin.detector_confidence_cap) unless a face is confirmed.
Rules are evaluated in three colour spaces simultaneously (YCrCb box, HSV band, Lab hue/chroma)
and a pixel must satisfy all; the mask is then cleaned morphologically and filtered by
connected-component area and compactness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from scipy import ndimage

from ..colorspace import (circular_mean_deg, lab_hue_deg, linear_to_lab, luminance, rgb_to_hsv,
                          signed_hue_diff, srgb_encode)
from ..types import clip01


@dataclass
class SkinResult:
    detected: bool = False
    confidence: float = 0.0
    area_fraction: float = 0.0
    face_count: int = 0
    mean_lab: tuple[float, float, float] | None = None
    hue_deg: float | None = None
    hue_std_deg: float | None = None
    chroma: float | None = None
    lightness: float | None = None
    median_y: float | None = None
    mean_linear_rgb: tuple[float, float, float] | None = None
    deviation: str = "none"           # none | toward_red | toward_magenta | toward_yellow
    deviation_deg: float = 0.0        # signed distance outside the plausible hue range
    reason: str = ""
    mask: np.ndarray | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "mask"}
        for k in ("hue_deg", "hue_std_deg", "chroma", "lightness", "median_y", "area_fraction", "deviation_deg", "confidence"):
            if d[k] is not None:
                d[k] = round(float(d[k]), 4)
        if d["mean_lab"]:
            d["mean_lab"] = [round(x, 2) for x in d["mean_lab"]]
        if d["mean_linear_rgb"]:
            d["mean_linear_rgb"] = [round(x, 5) for x in d["mean_linear_rgb"]]
        return d


def _faces(enc: np.ndarray, cascade_path) -> int:
    if not cascade_path or not Path(cascade_path).exists():
        return 0
    cc = cv2.CascadeClassifier(str(cascade_path))
    if cc.empty():
        return 0
    gray = cv2.cvtColor((enc * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    return int(len(cc.detectMultiScale(gray, 1.1, 5, minSize=(max(24, gray.shape[0] // 14),) * 2)))


def detect_skin(lin: np.ndarray, cfg) -> SkinResult:
    s = cfg.section("skin")
    if not s.get("enabled", True):
        return SkinResult(reason="detecção de pele desativada na configuração")
    enc = srgb_encode(lin).astype(np.float32)
    h, w = enc.shape[:2]
    ycc = cv2.cvtColor((enc * 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb)
    cr, cb = ycc[..., 1], ycc[..., 2]
    lab = linear_to_lab(lin)
    hue = lab_hue_deg(lab[..., 1], lab[..., 2])
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    hsv = rgb_to_hsv(enc)

    m = ((cr >= s["ycrcb"]["cr"][0]) & (cr <= s["ycrcb"]["cr"][1]) &
         (cb >= s["ycrcb"]["cb"][0]) & (cb <= s["ycrcb"]["cb"][1]) &
         (hue >= s["lab_hue"][0]) & (hue <= s["lab_hue"][1]) &
         (chroma >= s["lab_chroma"][0]) & (chroma <= s["lab_chroma"][1]) &
         (lab[..., 0] >= s["lab_l"][0]) & (lab[..., 0] <= s["lab_l"][1]) &
         (hsv[..., 1] >= s["hsv_sat"][0]) & (hsv[..., 1] <= s["hsv_sat"][1]) &
         (lin.max(-1) < 0.985))
    k = max(3, int(round(min(h, w) / 150)) | 1)
    m = ndimage.binary_opening(m, structure=np.ones((k, k)))
    labels, n = ndimage.label(m)
    if n == 0:
        return SkinResult(reason="nenhuma região com cor compatível com pele")
    areas = ndimage.sum(m, labels, index=np.arange(1, n + 1))
    min_px = s["min_area_fraction"] * h * w
    keep = np.zeros(n + 1, bool)
    slices = ndimage.find_objects(labels)
    for i, a in enumerate(areas, start=1):
        if a < min_px:
            continue
        sl = slices[i - 1]
        bbox = (sl[0].stop - sl[0].start) * (sl[1].stop - sl[1].start)
        if a / bbox >= 0.30:                      # compactness: rejects thin fringes / edges
            keep[i] = True
    mask = keep[labels]
    frac = float(mask.mean())
    if frac < s["min_area_fraction"]:
        return SkinResult(area_fraction=frac, reason="regiões candidatas pequenas ou irregulares demais")

    lab_m, hue_m = lab[mask], hue[mask]
    mean_hue, hue_std = circular_mean_deg(hue_m)
    mean_lab = lab_m.mean(0)
    y = luminance(lin)[mask]
    rgb_mean = lin[mask].mean(0)
    faces = _faces(enc, s.get("face_cascade"))

    area_score = clip01((frac - s["min_area_fraction"]) / max(s["full_area_fraction"] - s["min_area_fraction"], 1e-6))
    hue_score = clip01(1.0 - hue_std / 18.0)
    big = np.bincount(labels[mask]).max() / max(mask.sum(), 1)       # dominance of largest blob
    conf = 0.15 + 0.85 * (0.45 * area_score + 0.35 * hue_score + 0.20 * float(big))
    conf = min(conf, s["detector_confidence_cap"]) if faces == 0 else min(conf + 0.10, 0.98)
    # a "skin" region covering a large part of the frame is far more likely a warm wall / wood / tungsten cast
    huge = 1.0 - 0.9 * clip01((frac - s["implausible_area_start"]) / 0.30)
    conf *= huge

    lo, hi = s["hue_plausible"]
    if mean_hue < lo:
        dev, dd = ("toward_magenta" if mean_hue < lo - 12 else "toward_red"), float(mean_hue - lo)
    elif mean_hue > hi:
        dev, dd = "toward_yellow", float(mean_hue - hi)
    else:
        dev, dd = "none", 0.0
    return SkinResult(
        detected=True, confidence=float(conf), area_fraction=frac, face_count=faces,
        mean_lab=tuple(float(x) for x in mean_lab), hue_deg=mean_hue, hue_std_deg=hue_std,
        chroma=float(np.hypot(mean_lab[1], mean_lab[2])), lightness=float(mean_lab[0]),
        median_y=float(np.median(y)), mean_linear_rgb=tuple(float(x) for x in rgb_mean),
        deviation=dev, deviation_deg=dd,
        reason=(f"{frac * 100:.1f}% da imagem com cor de pele, matiz Lab {mean_hue:.0f}° (σ {hue_std:.0f}°)"
                + (f", {faces} rosto(s) confirmado(s)" if faces else ", sem confirmação por rosto")
                + (" — área grande demais para pele, confiança reduzida" if huge < 0.9 else "")),
        mask=mask,
    )


def skin_hue_after_gains(skin: SkinResult, matrix: np.ndarray) -> float | None:
    """Lab hue of the mean skin colour after a linear-sRGB 3x3 white-balance correction."""
    if not skin.detected or skin.mean_linear_rgb is None:
        return None
    rgb = np.clip(matrix @ np.asarray(skin.mean_linear_rgb), 0, None)
    lab = linear_to_lab(rgb[None, :])[0]
    return float(lab_hue_deg(lab[1], lab[2]))
