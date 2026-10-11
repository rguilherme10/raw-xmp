"""RAW access layer (LibRaw through rawpy).  Strictly read-only."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..colorspace import D65, SRGB_TO_XYZ
from .exif import read_metadata

try:
    import rawpy
except ImportError as exc:  # pragma: no cover
    raise ImportError("rawpy is required: pip install rawpy") from exc


class RawReadError(RuntimeError):
    pass


@dataclass
class RawInfo:
    path: str
    file_name: str
    file_size: int
    width: int
    height: int
    make: str | None = None
    camera: str | None = None
    lens: str | None = None
    iso: int | None = None
    aperture: float | None = None
    shutter: float | None = None
    focal_length: float | None = None
    orientation: int | None = None
    datetime: str | None = None
    black_level: list[int] = field(default_factory=list)
    white_level: int = 0
    camera_wb: list[float] = field(default_factory=list)     # multipliers normalised to G=1
    daylight_wb: list[float] = field(default_factory=list)
    xyz_to_cam: list[list[float]] | None = None              # 3x3, None when unavailable
    matrix_note: str = ""
    metadata_source: str = "none"
    raw_clip_fraction: float = 0.0                           # sensor-level saturation (true clipping)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def camera_neutral(self) -> np.ndarray | None:
        """Camera-space RGB of the as-shot illuminant (1/multipliers), G=1."""
        wb = np.asarray(self.camera_wb[:3], dtype=float)
        if wb.shape != (3,) or np.any(wb <= 0):
            return None
        n = 1.0 / wb
        return n / n[1]


def _norm_wb(wb) -> list[float]:
    wb = [float(x) for x in wb[:3]]
    if len(wb) == 3 and wb[1] > 0 and all(x > 0 for x in wb):
        return [x / wb[1] for x in wb]
    return []


def _neutral_from_mult(mult) -> np.ndarray | None:
    m = _norm_wb(mult)
    if not m:
        return None
    n = 1.0 / np.asarray(m)
    return n / n[1]


def select_xyz_to_cam(raw) -> tuple[list[list[float]] | None, str]:
    """Pick the XYZ→camera matrix and *verify its orientation against LibRaw's own daylight WB*.

    A correct matrix maps D65 to the camera response 1/daylight_multipliers.  We try the matrix as
    delivered and transposed and keep the one that reproduces it; if none does the matrix is unusable
    (returned as None) and white-balance estimation falls back to the camera's As Shot.
    The matrix is returned UNNORMALISED: raw camera units, so that  xyz = solve(M, neutral).
    """
    m = np.asarray(raw.rgb_xyz_matrix, dtype=float)[:3]
    src = "rgb_xyz_matrix"
    if np.all(np.abs(m) < 1e-9):
        # DNG: LibRaw leaves rgb_xyz_matrix empty but exposes camera->sRGB (color_matrix)
        c = np.asarray(raw.color_matrix, dtype=float)[:3, :3]
        if np.all(np.abs(c) < 1e-9) or abs(np.linalg.det(c)) < 1e-9:
            return None, "no matrix"
        m, src = np.linalg.inv(SRGB_TO_XYZ @ c), "color_matrix (DNG)"
    if abs(np.linalg.det(m)) < 1e-9:
        return None, "singular matrix"
    n_ref = _neutral_from_mult(raw.daylight_whitebalance)
    if n_ref is None:
        return m.tolist(), f"{src}, orientation unverified (no daylight WB)"
    for label, cand in (("as delivered", m), ("transposed", m.T)):
        n = cand @ D65
        if np.all(n > 0) and np.max(np.abs(n / n[1] - n_ref)) < 0.03:
            return cand.tolist(), f"{src}, {label}, verified against daylight WB"
    return None, f"{src}: neither orientation reproduces daylight WB — unusable"


def _clip_fraction(raw) -> float:
    try:
        img = raw.raw_image_visible
        step = max(1, int(max(img.shape) // 800))
        sub = img[::step, ::step]
        white = float(raw.white_level)
        return float((sub >= 0.995 * white).mean())
    except Exception:  # defensive: metadata must never block analysis
        return 0.0


class RawReader:
    """Open a RAW, expose metadata and decoded data.  Never writes to the file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _open(self):
        try:
            return rawpy.imread(str(self.path))
        except Exception as exc:  # LibRaw raises many exception types
            raise RawReadError(f"Não foi possível interpretar {self.path.name}: {str(exc).strip(chr(39)).lstrip('b').strip(chr(39)) or type(exc).__name__}") from exc

    def read_info(self) -> RawInfo:
        meta, src = read_metadata(self.path)
        with self._open() as raw:
            sz = raw.sizes
            warnings: list[str] = []
            wb = _norm_wb(raw.camera_whitebalance)
            if not wb:
                warnings.append("white balance da câmera indisponível")
            camera = " ".join(x for x in [meta.get("make"), meta.get("model")] if x)
            if meta.get("model") and meta.get("make") and str(meta["model"]).startswith(str(meta["make"])):
                camera = str(meta["model"])
            xyz_m, xyz_note = select_xyz_to_cam(raw)
            if xyz_m is None:
                warnings.append(f"matriz de cor inutilizável ({xyz_note}); WB não será corrigido")
            info = RawInfo(
                path=str(self.path), file_name=self.path.name, file_size=self.path.stat().st_size,
                width=int(sz.width), height=int(sz.height),
                make=meta.get("make"), camera=camera or None, lens=meta.get("lens"),
                iso=meta.get("iso"), aperture=_f(meta.get("aperture")), shutter=_f(meta.get("shutter")),
                focal_length=_f(meta.get("focal_length")), orientation=meta.get("orientation"),
                datetime=meta.get("datetime"),
                black_level=[int(x) for x in raw.black_level_per_channel],
                white_level=int(raw.white_level), camera_wb=wb,
                daylight_wb=_norm_wb(raw.daylight_whitebalance),
                xyz_to_cam=xyz_m, matrix_note=xyz_note, metadata_source=src,
                raw_clip_fraction=_clip_fraction(raw), warnings=warnings,
            )
        if not meta:
            info.warnings.append("EXIF não pôde ser lido; ISO/lente indisponíveis")
        return info

    def develop_linear(self, half_size: bool = True) -> np.ndarray:
        """Controlled render: camera WB, linear, sRGB primaries, float32 in [0, 1]."""
        with self._open() as raw:
            try:
                rgb = raw.postprocess(
                    half_size=half_size, use_camera_wb=True, no_auto_bright=True, gamma=(1, 1),
                    output_bps=16, output_color=rawpy.ColorSpace.sRGB, user_flip=0,
                    highlight_mode=rawpy.HighlightMode.Clip, bright=1.0,
                    demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
                )
            except Exception as exc:
                raise RawReadError(f"Falha ao desenvolver {self.path.name}: {exc}") from exc
        return rgb.astype(np.float32) / 65535.0

    def cache_key(self, max_dim: int) -> str:
        st = self.path.stat()
        h = hashlib.sha1(f"{self.path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{max_dim}".encode())
        return h.hexdigest()[:20]


def _f(x) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None
