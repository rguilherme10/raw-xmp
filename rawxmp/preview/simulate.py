"""APPROXIMATE preview of an XMP's effect (for calibration / visual sanity checks only).

This is NOT Adobe's rendering engine: Highlights/Shadows/Whites/Blacks/Contrast are modelled
with simple luminance-masked curves and HSL with hue-weighted HSV edits.  Compare *relative*
changes, never absolute looks.
"""
from __future__ import annotations

import numpy as np

from ..color.hsl import BANDS
from ..colorspace import luminance, rgb_to_hsv, signed_hue_diff, srgb_decode, srgb_encode


def _p(params: dict, k: str, d: float = 0.0) -> float:
    try:
        return float(params.get(k, d))
    except (TypeError, ValueError):
        return d


def _smooth(x):
    return x * x * (3 - 2 * x)


def default_render(lin: np.ndarray) -> np.ndarray:
    """'Before': plain sRGB encoding of the camera-WB linear render (no tone curve)."""
    return srgb_encode(lin).astype(np.float32)


def simulate(lin: np.ndarray, params: dict, curve: list[tuple[int, int]] | None = None,
             wb_matrix: np.ndarray | None = None, centers: dict | None = None) -> np.ndarray:
    img = lin.astype(np.float32)
    if wb_matrix is not None:
        img = np.clip(img @ wb_matrix.T.astype(np.float32), 0, None)
    img = np.clip(img * 2.0 ** _p(params, "Exposure2012"), 0, 1)

    y = luminance(img)
    v = srgb_encode(y)
    v2 = v.copy()
    sh, hi = _p(params, "Shadows2012") / 100, _p(params, "Highlights2012") / 100
    wh, bl, ct = _p(params, "Whites2012") / 100, _p(params, "Blacks2012") / 100, _p(params, "Contrast2012") / 100
    v2 += sh * 0.30 * (1 - v) ** 3 * v ** 0.3             # open shadows, leave blacks alone
    v2 += hi * 0.30 * v ** 3                              # compress highlights (hi ≤ 0)
    v2 = v2 / max(1 - wh * 0.20, 0.5)                     # white point
    b0 = -bl * 0.10
    v2 = (v2 - b0) / max(1 - b0, 0.5)                     # black point
    v2 = np.clip(v2, 0, 1)
    v2 += ct * 0.35 * (_smooth(v2) - v2) * 2
    v2 = np.clip(v2, 0, 1)
    ratio = np.where(v > 1e-5, srgb_decode(v2) / np.maximum(srgb_decode(v), 1e-6), 1.0)
    ratio = np.clip(ratio, 0, 8)[..., None]
    img = np.clip(img * ratio, 0, 1)
    enc = srgb_encode(img)

    if curve:
        xs = np.array([p[0] for p in curve]) / 255.0
        ys = np.array([p[1] for p in curve]) / 255.0
        enc = np.interp(enc, xs, ys)

    hsv = rgb_to_hsv(enc)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    sat = _p(params, "Saturation") / 100
    vib = _p(params, "Vibrance") / 100
    S2 = S * (1 + sat) * (1 + vib * (1 - S))
    centers = centers or {"red": 0, "orange": 30, "yellow": 60, "green": 120, "aqua": 180, "blue": 240,
                          "purple": 270, "magenta": 300}
    names = [b for b in BANDS if b in centers]
    cs = [centers[b] for b in names]
    dH = np.zeros_like(H)
    dS = np.zeros_like(H)
    dV = np.zeros_like(H)
    for i, n in enumerate(names):
        cap = n.capitalize()
        left = abs(signed_hue_diff(cs[i], cs[i - 1]))
        right = abs(signed_hue_diff(cs[(i + 1) % len(cs)], cs[i]))
        d = signed_hue_diff(H, cs[i])
        w = np.where(d < 0, np.clip(1 + d / left, 0, 1), np.clip(1 - d / right, 0, 1))
        dH += w * _p(params, f"HueAdjustment{cap}") / 100 * 30
        dS += w * _p(params, f"SaturationAdjustment{cap}") / 100
        dV += w * _p(params, f"LuminanceAdjustment{cap}") / 100 * 0.5
    H2 = (H + dH) % 360
    S2 = np.clip(S2 * (1 + dS), 0, 1)
    V2 = np.clip(V * (1 + dV * S), 0, 1)
    return _hsv_to_rgb(H2, S2, V2).astype(np.float32)


def _hsv_to_rgb(h, s, v):
    h6 = (h / 60.0) % 6
    i = np.floor(h6).astype(int)
    f = h6 - i
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], -1)
