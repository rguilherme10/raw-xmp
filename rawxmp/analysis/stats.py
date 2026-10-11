"""Descriptive image statistics (no decisions here)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from ..colorspace import luminance, srgb_encode

PCTS = (0.1, 1, 5, 50, 95, 99, 99.9)


@dataclass
class ImageStats:
    pct_y: dict[str, float]            # linear luminance percentiles
    pct_v: dict[str, float]            # perceptual (sRGB-encoded luminance) percentiles
    mean_y: float
    near_black_fraction: float
    near_white_fraction: float
    clip_fraction_any_channel: float
    clip_fraction_channel: dict[str, float]
    sensor_clip_fraction: float
    midtone_fraction: float
    global_contrast: float             # perceptual p95-p5
    dynamic_range_stops: float         # log2(p99.9 / max(p0.1, floor))
    local_contrast: float
    key: str                           # underexposed | low_key | high_key | high_contrast | normal
    histogram: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "histogram"}
        d["histogram_v"] = [round(x, 5) for x in self.histogram]
        return d


def _pct(a: np.ndarray, ps=PCTS) -> dict[str, float]:
    vals = np.percentile(a, ps)
    return {f"p{p:g}": float(v) for p, v in zip(ps, vals)}


def compute_stats(lin: np.ndarray, cfg, sensor_clip_fraction: float = 0.0, y: np.ndarray | None = None) -> ImageStats:
    a = cfg.section("analysis")
    ex = cfg.section("exposure")
    y = luminance(lin) if y is None else y
    v = srgb_encode(y)
    pct_y, pct_v = _pct(y), _pct(v)
    clip_t, black_t = a["clip_threshold"], a["near_black"]
    chan = {c: float((lin[..., i] >= clip_t).mean()) for i, c in enumerate("rgb")}
    any_clip = float((lin.max(-1) >= clip_t).mean())
    hist, _ = np.histogram(v, bins=a["histogram_bins"], range=(0, 1))
    hist = (hist / max(hist.sum(), 1)).tolist()

    sigma = max(2.0, 0.01 * max(y.shape))
    local = float(np.std(v - ndimage.gaussian_filter(v, sigma)))
    floor = 1e-4
    dr = float(np.log2(max(pct_y["p99.9"], floor) / max(pct_y["p0.1"], floor)))

    lk, hk = ex["low_key"], ex["high_key"]
    p1, p99 = max(pct_y["p1"], a.get("noise_floor", 0.003)), max(pct_y["p99"], floor)   # floor: below this is sensor noise
    ue = ex["underexposed"]
    if pct_y["p99.9"] <= ue["p999_max"] and pct_y["p50"] <= ue["p50_ratio"] * ex["target_median"]:
        key = "underexposed"        # nothing near white and dark median: not a creative low-key scene
    elif pct_y["p95"] <= lk["p95_max"] and pct_y["p50"] <= lk["p50_max"]:
        key = "low_key"
    elif pct_y["p5"] >= hk["p5_min"] and pct_y["p50"] >= hk["p50_min"]:
        key = "high_key"
    elif np.log2(p99 / p1) >= ex["high_contrast_stops"]:
        key = "high_contrast"
    else:
        key = "normal"
    return ImageStats(
        pct_y=pct_y, pct_v=pct_v, mean_y=float(y.mean()),
        near_black_fraction=float((y <= black_t).mean()),
        near_white_fraction=float((y >= clip_t).mean()),
        clip_fraction_any_channel=any_clip, clip_fraction_channel=chan,
        sensor_clip_fraction=float(sensor_clip_fraction),
        midtone_fraction=float(((v > 0.25) & (v < 0.75)).mean()),
        global_contrast=float(pct_v["p95"] - pct_v["p5"]), dynamic_range_stops=dr,
        local_contrast=local, key=key, histogram=hist,
    )
