"""Skin detection that survives colour casts: if nothing is found, neutralise the cast and look again."""
from __future__ import annotations

import numpy as np

from ..raw.reader import RawInfo
from ..white_balance.estimator import find_neutral_candidates, gains_to_srgb_matrix, srgb_to_camera_matrix
from .detector import SkinResult, detect_skin


def neutral_correction_matrix(lin: np.ndarray, info: RawInfo, cfg) -> np.ndarray | None:
    """Full-strength linear-sRGB matrix that makes the photo's own neutral surfaces neutral (None if no evidence)."""
    w = cfg.section("white_balance")
    mask, _ = find_neutral_candidates(lin, cfg, None)
    if mask.mean() < w["min_candidate_fraction"]:
        return None
    if info.xyz_to_cam is None:                       # JPEG: no camera space, neutralise directly in linear sRGB
        mean = lin[mask].mean(0)
        lo, hi = cfg.get("skin.retry_gain_range", [0.6, 1.8])
        g = np.clip(mean[1] / np.maximum(mean, 1e-6), lo, hi)
        return np.diag(g / g[1])
    xyz_to_cam = np.asarray(info.xyz_to_cam)
    mean_cam = srgb_to_camera_matrix(xyz_to_cam) @ lin[mask].mean(0)
    ratio = mean_cam / max(mean_cam[1], 1e-6)
    lo, hi = cfg.get("skin.retry_gain_range", [0.6, 1.8])
    gains = np.clip(1.0 / np.maximum(ratio, 1e-3), lo, hi)
    return gains_to_srgb_matrix(gains / gains[1], xyz_to_cam)


def detect_skin_robust(lin: np.ndarray, info: RawInfo, cfg, debug: dict | None = None) -> SkinResult:
    skin = detect_skin(lin, cfg, debug)
    if not cfg.get("skin.retry_with_neutral", True) or (skin.detected and skin.confidence >= cfg.get("skin.retry_below_confidence", 0.35)):
        return skin
    m = neutral_correction_matrix(lin, info, cfg)
    if m is None:
        return skin
    dbg2: dict | None = {} if debug is not None else None
    second = detect_skin(lin, cfg, dbg2, color_img=np.clip(lin @ m.T.astype(np.float32), 0, 1))
    if second.detected and second.confidence > skin.confidence:
        second.reason += " (detectada após neutralizar a dominante de cor da foto)"
        if debug is not None:
            debug.update(dbg2)
            debug["second_pass"] = True
        return second
    return skin
