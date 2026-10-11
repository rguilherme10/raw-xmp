"""White-balance estimation.

Temperature/Tint of Camera Raw describe the scene illuminant.  We take the camera's as-shot
neutral (reliable on Canon bodies) as the prior and only move away from it when the image
itself gives consistent evidence (many near-neutral pixels, tightly clustered, plausible
illuminant), always capped and attenuated by confidence and by skin plausibility.

Math: camera neutral n (RGB response of the illuminant) → XYZ = inv(XYZ→cam)·n → CCT & Duv
(CIE 1960 uv).  Tint = Duv · tint_per_duv.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..colorspace import SRGB_TO_XYZ, lab_hue_deg, linear_to_lab, luminance, xyz_to_cct_duv
from ..raw.reader import RawInfo
from ..skin.detector import SkinResult, skin_hue_after_gains
from ..types import Estimate, clamp, clip01


@dataclass
class WBResult:
    temperature: Estimate
    tint: Estimate
    mode: str                                   # "As Shot" | "Custom"
    as_shot_temperature: float | None
    as_shot_tint: float | None
    gains: np.ndarray = field(default_factory=lambda: np.ones(3))   # camera-space RGB gains (relative to as-shot)
    evidence: dict = field(default_factory=dict)
    matrix: np.ndarray = field(default_factory=lambda: np.eye(3))   # same correction as a linear-sRGB 3x3

    def to_dict(self) -> dict:
        return {
            "temperature": self.temperature.to_dict(), "tint": self.tint.to_dict(), "mode": self.mode,
            "as_shot": {"temperature": self.as_shot_temperature, "tint": self.as_shot_tint},
            "gains": [round(float(g), 4) for g in self.gains], "evidence": self.evidence,
        }


def srgb_to_camera_matrix(xyz_to_cam: np.ndarray) -> np.ndarray:
    """Matrix C with cam_wb = C @ srgb for the LibRaw render (rows normalised to sum 1, like rgb_cam)."""
    c = np.asarray(xyz_to_cam) @ SRGB_TO_XYZ
    return c / c.sum(axis=1, keepdims=True)


def gains_to_srgb_matrix(gains: np.ndarray, xyz_to_cam: np.ndarray) -> np.ndarray:
    c = srgb_to_camera_matrix(xyz_to_cam)
    return np.linalg.inv(c) @ np.diag(gains) @ c


def neutral_to_kelvin_tint(neutral: np.ndarray, xyz_to_cam: np.ndarray, cfg) -> tuple[float, float]:
    wbcfg = cfg.section("white_balance")
    xyz = np.linalg.solve(np.asarray(xyz_to_cam), neutral)
    if np.any(xyz <= 0):
        return float(wbcfg["default_kelvin"]), 0.0
    cct, duv = xyz_to_cct_duv(xyz)
    return cct + wbcfg["kelvin_offset"], duv * wbcfg["tint_per_duv"]


def find_neutral_candidates(lin: np.ndarray, cfg, skin: SkinResult | None = None) -> tuple[np.ndarray, dict]:
    """Near-neutral pixels = densest chromatic cluster among low-chroma, unclipped, mid-lightness pixels.

    Under a colour cast, grey surfaces are no longer at a*=b*=0 but still form one tight cluster; we
    locate that mode (2-D histogram in a*b*) instead of demanding absolute neutrality.
    """
    from scipy import ndimage
    w = cfg.section("white_balance")
    lab = linear_to_lab(lin)
    a, b = lab[..., 1], lab[..., 2]
    chroma = np.hypot(a, b)
    lo, hi = w["neutral_l_range"]
    pool = (chroma < w["neutral_chroma"]) & (lab[..., 0] > lo) & (lab[..., 0] < hi) & (lin.max(-1) < 0.985)
    if skin is not None and skin.mask is not None and skin.confidence > 0.5:
        pool &= ~skin.mask
    aux = {"chroma": chroma, "lab": lab, "pool_fraction": float(pool.mean()), "mode_ab": None}
    if pool.sum() < 50:
        return np.zeros(pool.shape, bool), aux
    ext = float(w["neutral_chroma"])
    hist, ae, be = np.histogram2d(a[pool], b[pool], bins=int(2 * ext / 2), range=[[-ext, ext], [-ext, ext]])
    hist = ndimage.gaussian_filter(hist, 1.0)
    ia, ib = np.unravel_index(np.argmax(hist), hist.shape)
    a0, b0 = (ae[ia] + ae[ia + 1]) / 2, (be[ib] + be[ib + 1]) / 2
    aux["mode_ab"] = (float(a0), float(b0))
    mask = pool & (np.hypot(a - a0, b - b0) < w["mode_radius"])
    return mask, aux


def estimate_white_balance(lin: np.ndarray, info: RawInfo, cfg, skin: SkinResult | None = None) -> WBResult:
    w = cfg.section("white_balance")
    n_asshot = info.camera_neutral
    xyz_to_cam = None if info.xyz_to_cam is None else np.asarray(info.xyz_to_cam)

    if n_asshot is None or xyz_to_cam is None:
        reason = "WB/matriz da câmera indisponíveis: mantido o padrão da câmera"
        t = Estimate(float(w["default_kelvin"]), 0.2, reason)
        return WBResult(t, Estimate(0.0, 0.2, reason), "As Shot", None, None)

    k0, tint0 = neutral_to_kelvin_tint(n_asshot, xyz_to_cam, cfg)
    if not (w["plausible_cct"][0] <= k0 <= w["plausible_cct"][1]) or abs(tint0) > w["max_as_shot_tint"]:
        reason = (f"Mapeamento matriz→Kelvin inconsistente ({k0:.0f} K, tint {tint0:+.0f}); "
                  "WB da câmera mantido sem tentativa de correção")
        return WBResult(Estimate(k0, 0.3, reason), Estimate(tint0, 0.3, reason), "As Shot", k0, tint0, np.ones(3),
                        {"as_shot_kelvin": round(k0, 1), "as_shot_tint": round(tint0, 2), "mapping_distrusted": True})
    mask, aux = find_neutral_candidates(lin, cfg, skin)
    frac = float(mask.mean())
    evidence = {"neutral_fraction": round(frac, 4), "neutral_pool_fraction": round(aux["pool_fraction"], 4),
                "neutral_mode_ab": None if aux["mode_ab"] is None else [round(v, 1) for v in aux["mode_ab"]], "as_shot_kelvin": round(k0, 1), "as_shot_tint": round(tint0, 2)}

    gains, matrix = np.ones(3), np.eye(3)
    k1, tint1 = k0, tint0
    if frac < w["min_candidate_fraction"]:
        conf = 0.9 if frac == 0 else 0.75       # as-shot is trusted; confidence of "no change" stays high
        reason = (f"Poucas regiões neutras ({frac * 100:.1f}%); mantido o WB da câmera "
                  f"({k0:.0f} K, tint {tint0:+.0f})")
        return WBResult(Estimate(k0, conf, reason), Estimate(tint0, conf, reason), "As Shot", k0, tint0, gains, evidence)

    C = srgb_to_camera_matrix(xyz_to_cam)
    mean_cam = C @ lin[mask].mean(0)                        # candidates in camera space (as-shot WB applied)
    ratio = mean_cam / max(mean_cam[1], 1e-6)               # neutral-candidate colour relative to green
    n_true = n_asshot * ratio                               # estimated illuminant response
    lab_m = aux["lab"][mask]
    spread = float(np.hypot(lab_m[:, 1].std(), lab_m[:, 2].std()))
    k_est, tint_est = neutral_to_kelvin_tint(n_true, xyz_to_cam, cfg)
    dk, dt = k_est - k0, tint_est - tint0

    cov = clip01((frac - w["min_candidate_fraction"]) / (w["full_candidate_fraction"] - w["min_candidate_fraction"]))
    coherence = clip01(1.0 - spread / 9.0)
    shift_penalty = float(np.exp(-(dk / 4000.0) ** 2 - (dt / 30.0) ** 2))   # large shifts look like real colour casts
    plausible = (w["plausible_cct"][0] <= k_est <= w["plausible_cct"][1]) and abs(tint_est - tint0) < 40
    conf = (0.25 + 0.75 * cov * coherence) * shift_penalty * (1.0 if plausible else 0.3)
    weight = clip01(conf) * w["image_trust"]

    # blend in log-ratio space, then cap by hard limits
    dk_w = clamp(dk * weight, -w["max_shift_kelvin"], w["max_shift_kelvin"])
    dt_w = clamp(dt * weight, -w["max_shift_tint"], w["max_shift_tint"])

    # skin-plausibility veto: don't push skin out of its healthy hue range
    note = ""
    if skin is not None and skin.detected and skin.confidence > 0.4 and weight > 0:
        g_full = ratio ** -1                                    # gains that neutralise the candidates
        g_try = g_full ** weight
        h_before = skin.hue_deg
        h_after = skin_hue_after_gains(skin, gains_to_srgb_matrix(g_try, xyz_to_cam))
        lo, hi = w["skin_hue_range"]
        if h_after is not None and not (lo <= h_after <= hi) and (lo <= h_before <= hi):
            dk_w, dt_w, weight = dk_w * 0.3, dt_w * 0.3, weight * 0.3
            note = f" Ajuste reduzido para não tirar a pele da faixa natural (matiz {h_before:.0f}°→{h_after:.0f}°)."

    ap = w["apply_threshold"]
    changed = (abs(dk_w) >= ap["kelvin"] or abs(dt_w) >= ap["tint"]) and conf >= w["min_confidence"]
    if changed:
        k1, tint1 = k0 + dk_w, tint0 + dt_w
        n_new = n_asshot * np.power(ratio, weight)
        gains = n_asshot / n_new                                # relative to as-shot render
        gains = gains / gains[1]
        mode = "Custom"
        matrix = gains_to_srgb_matrix(gains, xyz_to_cam)
        reason = (f"{frac * 100:.1f}% de pixels neutros indicam {k_est:.0f} K/tint {tint_est:+.0f} "
                  f"(câmera: {k0:.0f} K/{tint0:+.0f}); aplicado {dk_w:+.0f} K / {dt_w:+.0f} tint "
                  f"com confiança {conf:.2f}.{note}")
    elif conf < w["min_confidence"]:
        mode = "As Shot"
        reason = (f"Evidência da imagem fraca/incoerente (confiança {conf:.2f} < {w['min_confidence']}); "
                  f"mantido WB da câmera ({k0:.0f} K, tint {tint0:+.0f}); estimativa descartada: {k_est:.0f} K/{tint_est:+.0f}")
        conf = max(0.5, 1 - w["image_trust"] * 0.5)    # confidence of the *decision* "keep as shot"
    else:
        mode = "As Shot"
        reason = (f"WB da câmera ({k0:.0f} K, tint {tint0:+.0f}) coerente com {frac * 100:.1f}% de pixels neutros "
                  f"(estimativa {k_est:.0f} K/{tint_est:+.0f}); sem alteração.")
        conf = max(conf, 0.7)
    evidence.update({"estimated_kelvin": round(k_est, 1), "estimated_tint": round(tint_est, 2),
                     "candidate_spread_ab": round(spread, 2), "coverage": round(cov, 3),
                     "coherence": round(coherence, 3), "shift_penalty": round(shift_penalty, 3),
                     "weight": round(weight, 3)})
    return WBResult(Estimate(k1, conf, reason), Estimate(tint1, conf, reason), mode, k0, tint0, gains, evidence, matrix)
