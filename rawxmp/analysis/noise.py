"""Noise measured on the RAW (green Bayer plane), as a signal-dependent model  sigma^2(S) = a*S + b.

Photon (shot) noise grows with the signal, read noise is constant, so the *relative* noise sigma/S is largest in the
shadows.  Pushing exposure (+2 EV) moves those shadows into the mid-tones, which is why ISO alone under-predicts the
noise a photographer sees.  Estimation: 8x8 blocks, Laplacian residual (white noise: var(L)=36 sigma^2), the flattest
blocks per brightness bin (so texture is not mistaken for noise), least-squares fit of variance vs mean.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

_LAP = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)


@dataclass
class NoiseModel:
    a: float                 # shot-noise slope   (variance per unit of normalised signal)
    b: float                 # read-noise floor   (variance)
    valid: bool
    bins_used: int = 0

    def sigma(self, s: float) -> float:
        return float(np.sqrt(max(self.a * s + self.b, 1e-12)))

    def relative(self, s: float) -> float:
        s = max(s, 1e-4)
        return self.sigma(s) / s

    def to_dict(self) -> dict:
        return {"a": float(f"{self.a:.3g}"), "b": float(f"{self.b:.3g}"), "valid": self.valid, "bins_used": self.bins_used}


def estimate_noise_model(plane: np.ndarray, block: int = 8, flat_quantile: float = 0.2, n_bins: int = 12) -> NoiseModel:
    """plane: float array normalised to 0..1 (black subtracted, divided by white-black)."""
    p = plane.astype(np.float32)
    h, w = (p.shape[0] // block) * block, (p.shape[1] // block) * block
    if h < 4 * block or w < 4 * block:
        return NoiseModel(0.0, 0.0, False)
    lap = ndimage.convolve(p, _LAP, mode="nearest")
    p, lap = p[:h, :w], lap[:h, :w]
    mean = p.reshape(h // block, block, w // block, block).mean(axis=(1, 3)).ravel()
    var = ((lap ** 2).reshape(h // block, block, w // block, block).mean(axis=(1, 3)) / 36.0).ravel()
    ok = (mean > 0.002) & (mean < 0.9)                         # skip pure black and near-clipped blocks
    mean, var = mean[ok], var[ok]
    if mean.size < 200:
        return NoiseModel(0.0, 0.0, False)
    edges = np.quantile(mean, np.linspace(0, 1, n_bins + 1))
    xs, ys = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (mean >= lo) & (mean <= hi)
        if sel.sum() < 30:
            continue
        xs.append(float(mean[sel].mean()))
        ys.append(float(np.quantile(var[sel], flat_quantile)))        # flattest blocks of this brightness
    if len(xs) < 3:
        return NoiseModel(0.0, 0.0, False)
    x, y = np.asarray(xs), np.asarray(ys)
    A = np.stack([x, np.ones_like(x)], axis=1)
    (a, b), *_ = np.linalg.lstsq(A, y, rcond=None)
    a, b = max(float(a), 0.0), max(float(b), 0.0)
    return NoiseModel(a, b, valid=(a + b) > 0, bins_used=len(xs))
