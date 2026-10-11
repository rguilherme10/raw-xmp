"""Colour-space helpers (vectorised numpy).

Spaces used and why (see docs/ALGORITHM.md):
  * linear sRGB/Rec.709 primaries – physical quantities (exposure, clipping, white balance gains)
  * CIE XYZ / CIE 1960 uv           – illuminant temperature and tint
  * CIE Lab / LCh (D65)             – perceptual lightness, chroma, hue, skin analysis
  * HSV on sRGB-encoded values      – hue banding that mimics Camera Raw's HSL panel (approximation)
"""
from __future__ import annotations

import numpy as np

SRGB_TO_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                        [0.2126729, 0.7151522, 0.0721750],
                        [0.0193339, 0.1191920, 0.9503041]])
XYZ_TO_SRGB = np.linalg.inv(SRGB_TO_XYZ)
D65 = np.array([0.95047, 1.0, 1.08883])


def srgb_encode(lin: np.ndarray) -> np.ndarray:
    lin = np.clip(lin, 0.0, 1.0)
    return np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055)


def srgb_decode(enc: np.ndarray) -> np.ndarray:
    enc = np.clip(enc, 0.0, 1.0)
    return np.where(enc <= 0.04045, enc / 12.92, np.power((enc + 0.055) / 1.055, 2.4))


def luminance(lin_rgb: np.ndarray) -> np.ndarray:
    """Relative luminance Y (Rec.709 weights) of linear RGB (..., 3)."""
    return lin_rgb[..., 0] * 0.2126729 + lin_rgb[..., 1] * 0.7151522 + lin_rgb[..., 2] * 0.0721750


def lightness_from_y(y: np.ndarray) -> np.ndarray:
    """CIE L* in 0..100 from relative luminance Y."""
    y = np.clip(y, 0.0, None)
    f = np.where(y > 216 / 24389, np.cbrt(y), (24389 / 27 * y + 16) / 116)
    return 116 * f - 16


def linear_to_lab(lin_rgb: np.ndarray) -> np.ndarray:
    xyz = lin_rgb @ SRGB_TO_XYZ.T / D65
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def lab_hue_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.degrees(np.arctan2(b, a)) % 360.0


def rgb_to_hsv(enc_rgb: np.ndarray) -> np.ndarray:
    """H in degrees [0,360), S and V in [0,1] from sRGB-encoded values."""
    r, g, b = enc_rgb[..., 0], enc_rgb[..., 1], enc_rgb[..., 2]
    mx = enc_rgb.max(-1)
    mn = enc_rgb.min(-1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-9
    rc = nz & (mx == r)
    gc = nz & (mx == g) & ~rc
    bc = nz & ~rc & ~gc
    h[rc] = ((g - b)[rc] / d[rc]) % 6
    h[gc] = (b - r)[gc] / d[gc] + 2
    h[bc] = (r - g)[bc] / d[bc] + 4
    s = np.where(mx > 1e-9, d / np.maximum(mx, 1e-9), 0.0)
    return np.stack([h * 60.0, s, mx], axis=-1)


def circular_mean_deg(angles: np.ndarray, weights: np.ndarray | None = None) -> tuple[float, float]:
    """Return (mean angle in degrees, circular std in degrees)."""
    if angles.size == 0:
        return 0.0, 0.0
    w = np.ones_like(angles, dtype=float) if weights is None else weights.astype(float)
    sw = w.sum()
    if sw <= 0:
        return 0.0, 0.0
    rad = np.radians(angles)
    c, s = (w * np.cos(rad)).sum() / sw, (w * np.sin(rad)).sum() / sw
    mean = np.degrees(np.arctan2(s, c)) % 360.0
    r = min(max(np.hypot(c, s), 1e-9), 1.0)
    std = np.degrees(np.sqrt(-2.0 * np.log(r)))
    return float(mean), float(std)


def signed_hue_diff(a: np.ndarray | float, b: float) -> np.ndarray | float:
    """a - b wrapped into (-180, 180]."""
    return (np.asarray(a) - b + 180.0) % 360.0 - 180.0


# ---- illuminant temperature / tint -------------------------------------------------------

def xy_to_cct_mccamy(x: float, y: float) -> float:
    n = (x - 0.3320) / (0.1858 - y)
    return float(449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33)


def xyz_to_uv1960(xyz: np.ndarray) -> tuple[float, float]:
    X, Y, Z = xyz
    d = X + 15 * Y + 3 * Z
    return float(4 * X / d), float(6 * Y / d)


def planck_uv(T: float) -> tuple[float, float]:
    """Krystek (1985) approximation of the Planckian locus in CIE 1960 uv (1000–15000 K)."""
    u = (0.860117757 + 1.54118254e-4 * T + 1.28641212e-7 * T * T) / (1 + 8.42420235e-4 * T + 7.08145163e-7 * T * T)
    v = (0.317398726 + 4.22806245e-5 * T + 4.20481691e-8 * T * T) / (1 - 2.89741816e-5 * T + 1.61456053e-7 * T * T)
    return u, v


def xyz_to_cct_duv(xyz: np.ndarray) -> tuple[float, float]:
    """CCT (K) and Duv (positive = above the Planckian locus, i.e. greenish) of an XYZ white."""
    u, v = xyz_to_uv1960(xyz)
    X, Y, Z = xyz
    s = X + Y + Z
    cct0 = min(max(xy_to_cct_mccamy(X / s, Y / s), 1800.0), 20000.0)
    # refine by minimising distance to the locus around the McCamy estimate
    ts = np.linspace(max(1500.0, cct0 * 0.6), min(25000.0, cct0 * 1.6), 400)
    pu, pv = planck_uv_arr(ts)
    d2 = (pu - u) ** 2 + (pv - v) ** 2
    i = int(np.argmin(d2))
    lo, hi = max(i - 1, 0), min(i + 1, len(ts) - 1)
    ts2 = np.linspace(ts[lo], ts[hi], 200)
    pu, pv = planck_uv_arr(ts2)
    d2 = (pu - u) ** 2 + (pv - v) ** 2
    j = int(np.argmin(d2))
    sign = 1.0 if v > pv[j] else -1.0
    return float(ts2[j]), float(sign * np.sqrt(d2[j]))


def planck_uv_arr(T: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    u = (0.860117757 + 1.54118254e-4 * T + 1.28641212e-7 * T * T) / (1 + 8.42420235e-4 * T + 7.08145163e-7 * T * T)
    v = (0.317398726 + 4.22806245e-5 * T + 4.20481691e-8 * T * T) / (1 - 2.89741816e-5 * T + 1.61456053e-7 * T * T)
    return u, v
