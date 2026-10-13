"""Camera JPEG support: Exif, pixel decoding for analysis and XMP embedding (copy only — the original is never touched).

Lightroom / Camera Raw store develop settings of JPEG files INSIDE the file (XMP in an APP1 segment), not in a sidecar.
Embedding is done at the container level: the compressed image data is copied byte for byte, so pixels are identical.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

import numpy as np

from ..colorspace import srgb_decode
from .exif import parse_jpeg_exif, exiftool_metadata

XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
EXIF_HEADER = b"Exif\x00\x00"
MAX_SEGMENT = 65533


class JpegError(RuntimeError):
    pass


def _segments(data: bytes):
    """Yield (marker, start, end) for header segments up to (excluding) SOS; end is exclusive and includes the marker."""
    if data[:2] != b"\xff\xd8":
        raise JpegError("não é um JPEG (SOI ausente)")
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            raise JpegError(f"marcador inválido em {i}")
        m = data[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m == 0xDA or m == 0xD9:                       # SOS / EOI: header ends
            return
        (ln,) = struct.unpack(">H", data[i + 2:i + 4])
        yield m, i, i + 2 + ln
        i += 2 + ln


def extract_exif_tiff(path: str | Path) -> bytes | None:
    data = Path(path).read_bytes()
    for m, a, b in _segments(data):
        if m == 0xE1 and data[a + 4:a + 10] == EXIF_HEADER:
            return data[a + 10:b]
    return None


def extract_xmp(path: str | Path) -> str | None:
    """Embedded (standard) XMP packet of a JPEG as text, or None."""
    data = Path(path).read_bytes()
    for m, a, b in _segments(data):
        if m == 0xE1 and data[a + 4:a + 4 + len(XMP_HEADER)] == XMP_HEADER:
            return data[a + 4 + len(XMP_HEADER):b].decode("utf-8-sig", errors="replace")
    return None


def embed_xmp(src: str | Path, dst: str | Path, xmp_text: str) -> None:
    """Write ``dst`` = ``src`` with its XMP APP1 replaced by ``xmp_text``.  Image data is copied unchanged; atomic."""
    data = Path(src).read_bytes()
    payload = XMP_HEADER + xmp_text.encode("utf-8")
    if len(payload) > MAX_SEGMENT - 2:
        raise JpegError(f"XMP grande demais para um segmento APP1 ({len(payload)} bytes)")
    seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    out = bytearray(b"\xff\xd8")
    insert_at = None
    for m, a, b in _segments(data):
        if m == 0xE1 and data[a + 4:a + 4 + len(XMP_HEADER)] == XMP_HEADER:
            continue                                      # drop the old XMP packet
        if insert_at is None and not (0xE0 <= m <= 0xEF):
            insert_at = len(out)                          # right after the leading APPn segments
        out += data[a:b]
    if insert_at is None:
        raise JpegError("JPEG sem dados de imagem")
    out[insert_at:insert_at] = seg
    # everything after the last header segment (SOS + entropy-coded data + EOI) is copied verbatim
    last_end = 2
    for _m, _a, b in _segments(data):
        last_end = b
    out += data[last_end:]
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp{os.getpid()}")
    tmp.write_bytes(bytes(out))
    os.replace(tmp, dst)


def read_jpeg_metadata(path: Path) -> tuple[dict, str]:
    meta = exiftool_metadata(path)
    if meta:
        return meta, "exiftool"
    try:
        return parse_jpeg_exif(path), "exif"
    except (ValueError, struct.error, OSError, JpegError):
        return {}, "none"


def render_jpeg_linear(path: str | Path, max_dim: int = 1024) -> np.ndarray:
    """Decode to linear sRGB float32 (H, W, 3), downscaled.  Non-sRGB ICC profiles are converted to sRGB first."""
    import io

    from PIL import Image, ImageCms
    try:
        im = Image.open(path)
        im.draft("RGB", (max_dim * 2, max_dim * 2))       # fast DCT-domain downscale for big JPEGs
        icc = im.info.get("icc_profile")
        im = im.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise JpegError(f"Não foi possível interpretar {Path(path).name}: {exc}") from exc
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if "srgb" not in ImageCms.getProfileDescription(src).lower():
                im = ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except Exception:  # noqa: BLE001 — unreadable profile: assume sRGB
            pass
    w, h = im.size
    scale = max_dim / max(w, h)
    if scale < 1.0:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return srgb_decode(np.asarray(im, dtype=np.float32) / 255.0).astype(np.float32)


JPEG_EXTENSIONS = (".jpg", ".jpeg")


def is_jpeg(path: str | Path) -> bool:
    return Path(path).suffix.lower() in JPEG_EXTENSIONS


def read_jpeg_info(path: str | Path):
    """RawInfo-compatible description of a camera JPEG (no black/white level, no camera white balance)."""
    from PIL import Image

    from .reader import RawInfo, _f
    path = Path(path)
    meta, src = read_jpeg_metadata(path)
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception as exc:  # noqa: BLE001
        raise JpegError(f"Não foi possível interpretar {path.name}: {exc}") from exc
    camera = " ".join(x for x in [meta.get("make"), meta.get("model")] if x)
    if meta.get("model") and meta.get("make") and str(meta["model"]).startswith(str(meta["make"])):
        camera = str(meta["model"])
    warnings = [] if meta else ["EXIF não pôde ser lido; ISO/lente indisponíveis"]
    warnings.append("JPEG: calibrações (pele, âncora, ruído) são do RAW e não foram validadas para JPG")
    return RawInfo(path=str(path), file_name=path.name, file_size=path.stat().st_size, width=int(w), height=int(h),
                   make=meta.get("make"), camera=camera or None, lens=meta.get("lens"), iso=meta.get("iso"),
                   aperture=_f(meta.get("aperture")), shutter=_f(meta.get("shutter")), focal_length=_f(meta.get("focal_length")),
                   orientation=meta.get("orientation"), datetime=meta.get("datetime"), metadata_source=src,
                   matrix_note="JPEG", warnings=warnings)
