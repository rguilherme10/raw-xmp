"""Exposure / tonal slider estimation from multi-signal statistics (never histogram-only)."""
from __future__ import annotations

import numpy as np

from ..analysis.stats import ImageStats
from ..colorspace import srgb_encode
from ..skin.detector import SkinResult
from ..types import Estimate, clamp, clip01


def _post_v(stats: ImageStats, stops: float, key: str) -> float:
    return float(srgb_encode(np.clip(stats.pct_y[key] * 2.0 ** stops, 0, 1)))


def estimate_exposure(stats: ImageStats, skin: SkinResult, cfg, style_bias: float = 0.0) -> dict[str, Estimate]:
    ex = cfg.section("exposure")
    lo, hi = cfg.limit("exposure")
    p50 = max(stats.pct_y["p50"], 1e-4)
    target = ex["target_median"]

    # --- 1. evidence from global median --------------------------------------------------
    d_global = float(np.log2(target / p50))
    parts = [f"mediana Y={p50:.3f} vs alvo {target:.3f} ({d_global:+.2f} EV)"]
    delta = d_global

    # --- 2. evidence from skin (priority subject) -----------------------------------------
    if skin.detected and skin.median_y:
        d_skin = float(np.log2(ex["skin_target_y"] / max(skin.median_y, 1e-4)))
        w = ex["skin_weight"] * skin.confidence
        delta = (1 - w) * d_global + w * d_skin
        parts.append(f"pele Y={skin.median_y:.3f} sugere {d_skin:+.2f} EV (peso {w:.2f})")

    # --- 3. scene key damping ------------------------------------------------------------
    damp = ex["damping"].get(stats.key, ex["damping"]["normal"])
    delta *= damp
    if stats.key != "normal":
        parts.append(f"cena {stats.key} → correção atenuada ×{damp:.2f}")

    # --- 4. protect highlights: never brighten past the point where the bright subject clips -----
    # (a white shirt in a dark room: the median says "brighten", the highlights say "already full")
    conf = 0.85
    p99 = max(stats.pct_y["p99"], 1e-4)
    p999 = max(stats.pct_y["p99.9"], 1e-4)
    allowed_up = min(float(np.log2(ex["highlight_ceiling"] / p99)),
                     float(np.log2(0.99 / p999)) + ex["highlight_recovery_stops"])
    if stats.sensor_clip_fraction > 0.002 or stats.near_white_fraction > 0.01:
        allowed_up = min(allowed_up, 0.0)
        parts.append("highlights já cortados: sem aumento de exposição")
    allowed_up = max(allowed_up, 0.0)
    if delta > allowed_up:
        delta, conf = allowed_up, conf - 0.15
        parts.append(f"limitado pelas altas luzes (p99 Y={p99:.2f} → máx. +{allowed_up:.2f} EV)")
    delta = clamp(delta, -ex["max_correction_stops"], ex["max_correction_stops"])

    delta += style_bias
    if abs(delta) < ex["rounding"]:
        delta = 0.0
    delta = round(round(delta / ex["rounding"]) * ex["rounding"], 3)
    delta = clamp(delta, lo, hi)

    # confidence
    if stats.key not in ("normal", "underexposed"):
        conf -= 0.2
    if stats.key == "underexposed":
        parts.append("nada próximo do branco e mediana baixa: subexposição, não low-key")
    if abs(d_global) > 1.5:
        conf -= 0.15
    if skin.detected:
        conf += 0.07 * skin.confidence
    conf = clip01(conf)
    exposure = Estimate(delta, conf, "; ".join(parts))

    # --- Post-exposure predictions drive the remaining sliders ----------------------------
    e = delta                      # predictions include the style bias the user will actually see
    p99_v = _post_v(stats, e, "p99")
    p999_v = _post_v(stats, e, "p99.9")
    p5_v = _post_v(stats, e, "p5")
    p01_v = _post_v(stats, e, "p0.1")
    p95_v = _post_v(stats, e, "p95")
    # fraction clipped after exposure is estimated by shifting the clip threshold
    post_clip = max(stats.near_white_fraction,
                    _fraction_above(stats, 1.0 / 2.0 ** e)) if e > 0 else stats.near_white_fraction

    # Highlights ---------------------------------------------------------------------------
    h = ex["highlights"]
    need = clip01((p99_v - h["knee"]) / (1.0 - h["knee"]))
    pressure = clip01(post_clip / h["clip_fraction_full"])
    hv = -(0.5 * abs(h["max_pull"]) * need + 0.5 * abs(h["max_pull"]) * pressure)
    if stats.key == "low_key":
        hv *= 0.3
    hv += cfg.get("style.highlights_bias", 0)
    highlights = Estimate(
        round(hv), clip01(0.85 - 0.25 * (stats.key == "low_key")),
        f"p99 pós-exposição V={p99_v:.2f} (joelho {h['knee']}), pixels cortados {post_clip * 100:.2f}% → "
        + ("recuperar highlights" if hv < -5 else "highlights preservados"))

    # Shadows -------------------------------------------------------------------------------
    s = ex["shadows"]
    sv = s["max_lift"] * clip01((s["p5_target_v"] - p5_v) / s["p5_target_v"])
    if stats.key == "low_key":
        sv *= 0.4
    if p5_v > s["flat_p5_v"]:
        sv = 0.0
    sv += cfg.get("style.shadows_bias", 0)
    shadows = Estimate(round(sv), 0.8 - 0.2 * (stats.key == "low_key"),
                       f"p5 pós-exposição V={p5_v:.2f} (alvo ≥ {s['p5_target_v']}) → "
                       + ("abrir sombras" if sv > 5 else "sombras adequadas"))

    # Blacks --------------------------------------------------------------------------------
    b = ex["blacks"]
    crushed = stats.near_black_fraction if e <= 0 else max(stats.near_black_fraction * 0.5, 0.0)
    if p01_v > b["p01_target_v"] + b["p01_margin"]:
        bv = -b["deepen_scale"] * clip01((p01_v - b["p01_target_v"] - b["p01_margin"]) / 0.12)
        why = f"preto não atinge o fundo (p0.1 V={p01_v:.3f}) → aprofundar"
    elif crushed > b["crushed_fraction"] and stats.key != "low_key":
        bv = b["lift_scale"] * clip01(crushed / 0.05)
        why = f"{crushed * 100:.1f}% de pixels esmagados em preto → aliviar"
    else:
        bv, why = 0.0, "ponto de preto adequado"
    blacks = Estimate(round(bv), 0.75, why)

    # Whites --------------------------------------------------------------------------------
    wcfg = ex["whites"]
    if stats.key == "low_key":
        wv, wwhy = 0.0, "cena low-key: branco inalterado"
    elif post_clip > 0.005:
        wv, wwhy = -min(30.0, 1000 * post_clip), f"{post_clip * 100:.2f}% cortado → reduzir brancos"
    else:
        wv = clamp((wcfg["p999_target_v"] - p999_v) * wcfg["gain"], -20, wcfg["max_gain"])
        wwhy = f"p99.9 pós-exposição V={p999_v:.2f} (alvo {wcfg['p999_target_v']})"
    whites = Estimate(round(wv), 0.7, wwhy)

    # Contrast ------------------------------------------------------------------------------
    c = ex["contrast"]
    spread = p95_v - p5_v
    cv = (c["target_spread"] - spread) * c["gain"]
    if stats.local_contrast < c["local_low"]:
        cv += 4
    if stats.key in ("high_contrast",):
        cv = min(cv, 0.0)
    cv += cfg.get("style.contrast_bias", 0)
    contrast = Estimate(round(cv), 0.65 if stats.key == "normal" else 0.5,
                        f"amplitude perceptual p95−p5={spread:.2f} (alvo {c['target_spread']}), "
                        f"contraste local {stats.local_contrast:.3f}, cena {stats.key}")
    return {"exposure": exposure, "highlights": highlights, "shadows": shadows,
            "whites": whites, "blacks": blacks, "contrast": contrast}


def _fraction_above(stats: ImageStats, y_thresh: float) -> float:
    """Approximate fraction of pixels with Y above a threshold via the stored percentiles."""
    keys = [(0.1, "p0.1"), (1, "p1"), (5, "p5"), (50, "p50"), (95, "p95"), (99, "p99"), (99.9, "p99.9")]
    xs = [stats.pct_y[k] for _, k in keys]
    ps = [p for p, _ in keys]
    if y_thresh >= xs[-1]:
        return 0.0 if y_thresh > 1.0 else 0.001
    if y_thresh <= xs[0]:
        return 1.0
    pct_below = float(np.interp(y_thresh, xs, ps))
    return max(0.0, 1.0 - pct_below / 100.0)
