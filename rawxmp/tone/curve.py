"""Adaptive tone curve generation (display-referred 0..255 point curve, Camera Raw PV2012)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..types import Estimate, clamp, clip01


@dataclass
class CurveResult:
    points: list[tuple[int, int]]
    notes: list[str] = field(default_factory=list)
    confidence: float = 0.7

    def to_dict(self) -> dict:
        return {"points": [list(p) for p in self.points], "notes": self.notes, "confidence": self.confidence}


def enforce_monotonic(ys: list[float], min_slope: float, xs: list[float]) -> list[float]:
    ys = list(ys)
    for i in range(1, len(ys)):
        floor = ys[i - 1] + min_slope * (xs[i] - xs[i - 1])
        if ys[i] < floor:
            ys[i] = floor
    if ys[-1] > xs[-1]:               # keep the white end anchored; pull earlier points down
        ys[-1] = xs[-1]
        for i in range(len(ys) - 2, -1, -1):
            ceil = ys[i + 1] - min_slope * (xs[i + 1] - xs[i])
            if ys[i] > ceil:
                ys[i] = ceil
    return ys


def build_curve(cfg, estimates: dict[str, Estimate], stats, skin) -> CurveResult:
    tc = cfg.section("tone_curve")
    xs = [float(x) for x in tc["x_points"]]
    base = {int(p[0]): float(p[1]) for p in cfg.get("style.tone_curve.points")}
    ys = [float(np.interp(x, [k for k in sorted(base)], [base[k] for k in sorted(base)])) for x in xs]
    ys = [base.get(int(x), y) for x, y in zip(xs, ys)]
    notes: list[str] = []

    def idx(x):  # nearest x index
        return int(np.argmin([abs(v - x) for v in xs]))

    # image-driven S-curve amount: positive (more contrast) when the contrast slider is positive
    c = estimates["contrast"].value - cfg.get("style.contrast_bias", 0)     # image-measured part only
    s_levels = clamp(c * tc["contrast_to_levels"], -tc["max_s_levels"] / 2, tc["max_s_levels"])
    if abs(s_levels) >= 0.5:
        ys[idx(64)] -= s_levels
        ys[idx(192)] += s_levels
        notes.append(f"S-curve {s_levels:+.1f} níveis (contraste {c:+.0f})")

    # shadows: crushed → lift slightly; flat blacks handled by Blacks slider, not the curve
    lift = clip01(stats.near_black_fraction / 0.05) * tc["shadow_lift_levels"]
    if lift > 0.5 and stats.key != "low_key":
        ys[idx(32)] += lift
        ys[idx(64)] += lift * 0.5
        notes.append(f"sombras esmagadas ({stats.near_black_fraction * 100:.1f}%): +{lift:.1f} níveis em 32")

    # highlights: roll off when clipping pressure exists
    hv = estimates["highlights"].value - cfg.get("style.highlights_bias", 0)
    if hv < -15:
        r = clip01(-hv / 60) * tc["highlight_rolloff_levels"]
        ys[idx(192)] -= r
        notes.append(f"roll-off de highlights −{r:.1f} níveis em 192")

    # skin protection: slope between 128 and 192 stays gentle
    if skin.detected:
        i0, i1 = idx(128), idx(192)
        lo, hi = tc["skin_slope"]
        slope = (ys[i1] - ys[i0]) / (xs[i1] - xs[i0])
        sl = clamp(slope, lo, hi)
        if abs(sl - slope) > 1e-6:
            ys[i1] = ys[i0] + sl * (xs[i1] - xs[i0])
            notes.append(f"pele detectada: inclinação 128–192 limitada a {sl:.2f}")

    # global safety: bounded deviation, anchored ends, monotonic
    dev = cfg.get("limits.tone_curve_max_deviation", 14)
    ys = [clamp(y, x - dev, x + dev) for x, y in zip(xs, ys)]
    ys[-1] = xs[-1]
    ys = enforce_monotonic(ys, tc["min_slope"], xs)
    ys = [clamp(y, 0, 255) for y in ys]
    pts = [(int(round(x)), int(round(y))) for x, y in zip(xs, ys)]
    return CurveResult(pts, notes, confidence=0.7 if stats.key == "normal" else 0.55)
