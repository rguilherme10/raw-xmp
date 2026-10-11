"""Analysis render: small linear-sRGB float image (cached on request)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .reader import RawReader


def render_for_analysis(reader: RawReader, max_dim: int = 1024, cache_dir: str | Path | None = None) -> np.ndarray:
    """Half-size demosaic + area downscale → linear sRGB float32 (H, W, 3), camera WB applied.

    Never decodes at full resolution: analysis statistics converge long before 1024 px.
    """
    cache_file = None
    if cache_dir:
        cache_file = Path(cache_dir) / f"{reader.cache_key(max_dim)}.npy"
        if cache_file.exists():
            try:
                return np.load(cache_file)
            except (OSError, ValueError):
                pass
    img = reader.develop_linear(half_size=True)
    h, w = img.shape[:2]
    scale = max_dim / max(h, w)
    if scale < 1.0:
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    img = np.ascontiguousarray(img, dtype=np.float32)
    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp.npy")
        np.save(tmp, img)
        tmp.replace(cache_file)
    return img
