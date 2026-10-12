"""Sharpening and noise reduction: style baseline + noise measured on the RAW and the exposure push."""
from __future__ import annotations

import math

from ..analysis.noise import NoiseModel
from ..types import Estimate, clamp


def estimate_detail(iso: int | None, cfg, noise: NoiseModel | None = None, exposure: float = 0.0) -> dict[str, Estimate]:
    d = cfg.section("detail")
    st = cfg.section("style")
    lim = cfg.limit
    if not iso:
        return {
            "sharpness": Estimate(st["sharpening"]["amount"], 0.4, "ISO desconhecido: nitidez do estilo"),
            "luminance_nr": Estimate(st["noise_reduction"]["luminance"], 0.4, "ISO desconhecido: NR do estilo"),
            "color_nr": Estimate(st["noise_reduction"]["color"], 0.4, "ISO desconhecido: NR do estilo"),
            "masking": Estimate(st["sharpening"]["masking"], 0.4, "ISO desconhecido"),
        }
    push = max(0.0, exposure) * d.get("push_weight", 0.8)            # shadows lifted by the exposure push look like higher ISO
    stops = max(0.0, math.log2(iso / d["iso_base"]) + push)
    sharp = clamp(st["sharpening"]["amount"] - d["sharpness_drop_per_stop"] * stops, *lim("sharpness"))
    mask = clamp(st["sharpening"]["masking"] + d["edge_masking_per_stop"] * stops, 0, 80)
    cnr = clamp(st["noise_reduction"]["color"] + d["color_nr_per_stop"] * stops, *lim("color_nr"))
    why = (f"ISO {iso} + empurrão de exposição {exposure:+.2f} EV = {stops:.1f} stops acima de ISO {d['iso_base']} "
           f"(ISO efetivo ≈ {iso * 2 ** (stops - math.log2(iso / d['iso_base'])) if stops else iso:.0f})")
    rel_est = Estimate(None, 0.0, "ruído não medido")
    if noise is not None and noise.valid:
        ref_raw = clamp(d["noise_reference_y"] / (2.0 ** exposure), 0.003, 0.9)       # raw level that ends up at the reference tone
        rel = noise.relative(ref_raw)
        rel_est = Estimate(round(rel, 5), 0.7, "ruído relativo σ/S no tom de referência")
        lnr = st["noise_reduction"]["luminance"] + clamp((rel - d["noise_free_relative"]) * d["noise_gain"], 0, 100)
        lnr = clamp(lnr, *lim("luminance_nr"))
        lnr_est = Estimate(round(lnr), 0.7, f"ruído medido no RAW: {rel * 100:.1f}% no tom de referência (Y_saída {d['noise_reference_y']}, "
                                            f"nível do sensor {ref_raw:.3f}); NR do estilo {st['noise_reduction']['luminance']} + {lnr - st['noise_reduction']['luminance']:.0f}")
    else:
        lnr = clamp(st["noise_reduction"]["luminance"] + d["luminance_nr_per_stop"] * stops, *lim("luminance_nr"))
        lnr_est = Estimate(round(lnr), 0.5, why + " (ruído não pôde ser medido)")
    return {
        "sharpness": Estimate(round(sharp), 0.8, why + ": nitidez reduzida com o ruído"),
        "luminance_nr": lnr_est,
        "color_nr": Estimate(round(cnr), 0.8, why),
        "masking": Estimate(round(mask), 0.8, why + ": máscara de bordas ampliada"),
        "noise_relative": rel_est,
    }
