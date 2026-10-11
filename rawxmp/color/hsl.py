"""Per-colour-band analysis (HSL panel) and evidence-gated corrections."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..colorspace import circular_mean_deg, linear_to_lab, rgb_to_hsv, signed_hue_diff, srgb_encode
from ..skin.detector import SkinResult
from ..types import Estimate, clamp, clip01

BANDS = ["red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"]
SKIN_BANDS = {"red", "orange", "yellow"}


@dataclass
class BandStats:
    name: str
    fraction: float = 0.0
    mean_hue: float = 0.0           # absolute degrees (HSV)
    hue_offset: float = 0.0         # signed distance from band centre
    hue_std: float = 0.0
    mean_saturation: float = 0.0
    mean_lightness: float = 0.0     # CIE L*
    mean_chroma: float = 0.0
    clipped_fraction: float = 0.0   # share of band pixels with a saturated/clipped channel

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def analyze_bands(lin: np.ndarray, cfg) -> dict[str, BandStats]:
    c = cfg.section("color")
    centers = c["band_centers"]
    enc = srgb_encode(lin)
    hsv = rgb_to_hsv(enc)
    lab = linear_to_lab(lin)
    H, S, V = hsv[..., 0].ravel(), hsv[..., 1].ravel(), hsv[..., 2].ravel()
    L = lab[..., 0].ravel()
    C = np.hypot(lab[..., 1], lab[..., 2]).ravel()
    clipped = ((enc.max(-1) > 0.985) & (hsv[..., 1] > 0.5)).ravel() | ((enc.min(-1) < 0.01) & (hsv[..., 1] > 0.85)).ravel()
    chromatic = (S >= c["chroma_min_sat"]) & (V >= 0.08)
    n = H.size
    names = [b for b in BANDS if b in centers]
    cs = np.array([centers[b] for b in names], dtype=float)
    out: dict[str, BandStats] = {}
    for i, name in enumerate(names):
        centre = cs[i]
        left = abs(signed_hue_diff(centre, cs[i - 1]))
        right = abs(signed_hue_diff(cs[(i + 1) % len(cs)], centre))
        d = signed_hue_diff(H, centre)
        # triangular membership: 1 at the centre, 0 at the neighbouring band centre
        wl = np.clip(1 + d / left, 0, 1) * (d < 0)
        wr = np.clip(1 - d / right, 0, 1) * (d >= 0)
        w = (wl + wr) * chromatic
        sw = w.sum()
        if sw <= 0:
            out[name] = BandStats(name)
            continue
        sel = w > 0.5
        m_h, s_h = circular_mean_deg(H[sel], w[sel]) if sel.any() else (centre, 0.0)
        out[name] = BandStats(
            name=name, fraction=float(sw / n), mean_hue=m_h, hue_offset=float(signed_hue_diff(m_h, centre)),
            hue_std=s_h, mean_saturation=float((w * S).sum() / sw), mean_lightness=float((w * L).sum() / sw),
            mean_chroma=float((w * C).sum() / sw), clipped_fraction=float((w * clipped).sum() / sw),
        )
    return out


@dataclass
class HSLResult:
    hue: dict[str, Estimate]
    saturation: dict[str, Estimate]
    luminance: dict[str, Estimate]
    bands: dict[str, BandStats]
    skin_protected: bool = False

    def to_dict(self) -> dict:
        return {
            "bands": {k: v.to_dict() for k, v in self.bands.items()},
            "hue": {k: v.to_dict() for k, v in self.hue.items()},
            "saturation": {k: v.to_dict() for k, v in self.saturation.items()},
            "luminance": {k: v.to_dict() for k, v in self.luminance.items()},
            "skin_protected": self.skin_protected,
        }


def estimate_hsl(bands: dict[str, BandStats], skin: SkinResult, cfg) -> HSLResult:
    """Evidence-gated corrections only: no evidence → value 0."""
    c = cfg.section("color")
    mh, ms, ml = c["max_adjust"]["hue"], c["max_adjust"]["saturation"], c["max_adjust"]["luminance"]
    hue, sat, lum = {}, {}, {}
    protect = skin.detected
    for name, b in bands.items():
        hue[name] = Estimate(0, 0.9, "sem evidência de desequilíbrio")
        sat[name] = Estimate(0, 0.9, "sem evidência de desequilíbrio")
        lum[name] = Estimate(0, 0.9, "sem evidência de desequilíbrio")
        if b.fraction < c["min_band_fraction"]:
            for e in (hue[name], sat[name], lum[name]):
                e.reason = f"faixa pouco presente ({b.fraction * 100:.1f}% < {c['min_band_fraction'] * 100:.0f}%)"
            continue
        presence = clip01(b.fraction / (4 * c["min_band_fraction"]))
        # --- saturation: only reduce when over-saturated / clipped ------------------------
        sv, why = 0.0, ""
        if b.mean_saturation > c["sat_high"]:
            sv -= (b.mean_saturation - c["sat_high"]) * c["sat_gain"]
            why = f"saturação média {b.mean_saturation:.2f} > {c['sat_high']}"
        if b.clipped_fraction > c["clip_fraction"]:
            sv -= 15 * clip01(b.clipped_fraction / 0.3)
            why += f"; {b.clipped_fraction * 100:.0f}% das cores com canal cortado"
        if sv:
            sat[name] = Estimate(sv * presence, 0.45 + 0.4 * presence, why.strip("; "))
        # --- hue: only compact bands with a clear drift ----------------------------------
        off = b.hue_offset
        if abs(off) > c["hue_dead_zone"] and b.hue_std <= c["hue_std_max"]:
            hv = -(abs(off) - c["hue_dead_zone"]) * np.sign(off) * c["hue_gain"]
            hue[name] = Estimate(hv * presence, 0.4 + 0.4 * presence,
                                 f"matiz médio deslocado {off:+.0f}° e faixa compacta (σ {b.hue_std:.0f}°)")
        # --- luminance: lift deep, strongly coloured regions -----------------------------
        if b.mean_lightness < c["luminance_dark"] and b.mean_chroma > 25:
            lum[name] = Estimate((c["luminance_dark"] - b.mean_lightness) * c["luminance_gain"] * 4 * presence,
                                 0.45, f"faixa escura (L* {b.mean_lightness:.0f}) e saturada")
        for d, lim in ((hue, mh), (sat, ms), (lum, ml)):
            d[name].value = clamp(d[name].value, -lim, lim)

    # --- skin protection -------------------------------------------------------------------
    if protect:
        att = 1 - c["skin_protection"] * skin.confidence
        cap = c["skin_cap"]
        for d in (hue, sat, lum):
            for name in SKIN_BANDS & set(d):
                e = d[name]
                if e.value:
                    e.value = clamp(e.value * att, -cap, cap)
                    e.reason += f" (atenuado: pele detectada, confiança {skin.confidence:.2f})"
        # explicit, small skin-driven nudge of Orange hue
        if skin.deviation in ("toward_red", "toward_magenta") and "orange" in hue:
            nudge = min(c["skin_hue_nudge"], abs(skin.deviation_deg) * 0.25) * skin.confidence
            hue["orange"] = Estimate(hue["orange"].value + nudge, skin.confidence * 0.7,
                                     f"pele {skin.deviation.replace('_', ' ')} ({skin.deviation_deg:+.0f}°): Orange → amarelo")
        elif skin.deviation == "toward_yellow" and "orange" in hue:
            nudge = min(c["skin_hue_nudge"], abs(skin.deviation_deg) * 0.25) * skin.confidence
            hue["orange"] = Estimate(hue["orange"].value - nudge, skin.confidence * 0.7,
                                     f"pele amarelada ({skin.deviation_deg:+.0f}°): Orange → vermelho")
    for d in (hue, sat, lum):
        for e in d.values():
            e.value = int(round(e.value))
    return HSLResult(hue, sat, lum, bands, protect)
