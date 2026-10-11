"""ISO-adaptive sharpening and noise reduction (style provides the baseline)."""
from __future__ import annotations

import math

from ..types import Estimate, clamp


def estimate_detail(iso: int | None, cfg) -> dict[str, Estimate]:
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
    stops = max(0.0, math.log2(iso / d["iso_base"]))
    sharp = clamp(st["sharpening"]["amount"] - d["sharpness_drop_per_stop"] * stops, *lim("sharpness"))
    mask = clamp(st["sharpening"]["masking"] + d["edge_masking_per_stop"] * stops, 0, 80)
    lnr = clamp(st["noise_reduction"]["luminance"] + d["luminance_nr_per_stop"] * stops, *lim("luminance_nr"))
    cnr = clamp(st["noise_reduction"]["color"] + d["color_nr_per_stop"] * stops, *lim("color_nr"))
    why = f"ISO {iso} = {stops:.1f} stops acima de ISO {d['iso_base']}"
    return {
        "sharpness": Estimate(round(sharp), 0.8, why + ": nitidez reduzida com o ruído"),
        "luminance_nr": Estimate(round(lnr), 0.8, why),
        "color_nr": Estimate(round(cnr), 0.8, why),
        "masking": Estimate(round(mask), 0.8, why + ": máscara de bordas ampliada"),
    }
