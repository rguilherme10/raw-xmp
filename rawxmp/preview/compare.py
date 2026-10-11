"""Before/after side-by-side image + histograms (calibration aid)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..config import Config
from ..correction.engine import analyze_raw
from ..raw.reader import RawReader
from ..raw.render import render_for_analysis
from ..xmp.parser import parse_xmp
from .simulate import default_render, simulate


def make_comparison(raw_path: str | Path, out_png: str | Path, cfg: Config, xmp: str | Path | None = None,
                    max_dim: int = 900) -> Path:
    dec = analyze_raw(raw_path, cfg)
    lin = render_for_analysis(RawReader(raw_path), max_dim)
    if xmp:
        m = parse_xmp(xmp)
        params = {k: v for k, v in m.attrs.items()}
        curve = m.curves.get("ToneCurvePV2012")
        matrix = None                       # WB of an external XMP cannot be reproduced → not simulated
        note = f"XMP externo: {Path(xmp).name} (WB não simulado)"
    else:
        params = {k: v for k, v in dec.params.items()}
        curve = dec.curve.points
        matrix = dec.wb.matrix if dec.wb.mode == "Custom" else None
        note = "ajustes calculados (simulação aproximada)"
    before = default_render(lin)
    after = simulate(lin, params, curve, matrix, cfg.get("color.band_centers"))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        side = np.concatenate([before, after], axis=1)
        cv2.imwrite(str(out_png), cv2.cvtColor((side * 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
        return out_png
    fig = plt.figure(figsize=(12, 6.2), dpi=110)
    gs = fig.add_gridspec(2, 2, height_ratios=[4, 1.5])
    for i, (img, title) in enumerate(((before, "Antes (render linear → sRGB)"), (after, f"Depois — {note}"))):
        ax = fig.add_subplot(gs[0, i])
        ax.imshow(np.clip(img, 0, 1))
        ax.set_title(title, fontsize=9)
        ax.axis("off")
        hx = fig.add_subplot(gs[1, i])
        for c, col in enumerate(("#d62728", "#2ca02c", "#1f77b4")):
            h, _ = np.histogram(img[..., c], bins=64, range=(0, 1))
            hx.plot(np.linspace(0, 1, 64), h / h.max(), color=col, lw=1)
        hx.set_yticks([])
        hx.set_xlim(0, 1)
    fig.suptitle(f"{Path(raw_path).name} — Exp {dec.params['Exposure2012']:+.2f}  Temp {dec.wb.mode}", fontsize=10)
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)
    return out_png
