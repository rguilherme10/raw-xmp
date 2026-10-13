"""Generate synthetic Bayer RAW files (DNG-structured, uncompressed 16-bit) for tests.

The files are real TIFF/DNG containers decodable by LibRaw (rawpy). They carry Exif
tags (ISO, aperture, shutter, focal length, lens, date, make/model) so the metadata
layer can be exercised. They are NOT Canon CR2 files: they only stand in for them.
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

import numpy as np

# XYZ(D65)->camera matrix of a plausible camera (rows = camera R,G,B). Fixed for tests.
XYZ_TO_CAM = np.array([[0.9, -0.2, -0.1], [-0.35, 1.15, 0.2], [0.02, 0.1, 0.65]])
SRGB_TO_XYZ = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
# Normalise rows so that D65 white (sRGB 1,1,1) maps to camera (1,1,1) — as DNG ColorMatrix expects.
XYZ_TO_CAM = XYZ_TO_CAM / (XYZ_TO_CAM @ SRGB_TO_XYZ).sum(axis=1, keepdims=True)
SRGB_TO_CAM = XYZ_TO_CAM @ SRGB_TO_XYZ


def _pack_value(typ: int, val) -> bytes:
    vals = val if isinstance(val, (list, tuple)) else [val]
    if typ in (1, 2, 7):
        return bytes(val)
    if typ == 3:
        return struct.pack("<%dH" % len(vals), *vals)
    if typ == 4:
        return struct.pack("<%dI" % len(vals), *vals)
    if typ == 5:
        return b"".join(struct.pack("<II", a, b) for a, b in vals)
    if typ == 10:
        return b"".join(struct.pack("<ii", a, b) for a, b in vals)
    raise ValueError(typ)


def _ifd_size(n: int) -> int:
    return 2 + n * 12 + 4


def _build_ifd(entries, extra_base: int, extra: bytearray) -> bytes:
    """Serialize one IFD; values >4 bytes are appended to ``extra`` (absolute base offset)."""
    out = struct.pack("<H", len(entries))
    for tag, typ, count, val in sorted(entries, key=lambda e: e[0]):
        raw = _pack_value(typ, val)
        if len(raw) <= 4:
            out += struct.pack("<HHI", tag, typ, count) + raw.ljust(4, b"\0")
        else:
            out += struct.pack("<HHII", tag, typ, count, extra_base + len(extra))
            extra += raw + (b"\0" if len(raw) % 2 else b"")
    return out + struct.pack("<I", 0)


def _rat(x: float, den: int = 1000):
    return (int(round(x * den)), den)


def write_dng(path: Path, bayer: np.ndarray, *, black=256, white=16383, neutral=(0.5, 1.0, 0.65),
              iso=400, fnumber=2.8, exposure_time=1 / 200, focal=50.0, make="Canon",
              model="Canon EOS SYNTH", lens="EF50mm f/1.8 STM", date="2024:05:01 10:20:30",
              orientation=1) -> None:
    """Write an uncompressed RGGB DNG (TIFF layout) with an Exif IFD."""
    h, w = bayer.shape
    data = bayer.astype("<u2").tobytes()
    asc = lambda s: s.encode() + b"\0"
    exif_entries = [
        (33434, 5, 1, [_rat(exposure_time, 100000)]),
        (33437, 5, 1, [_rat(fnumber, 10)]),
        (34855, 3, 1, iso),
        (36867, 2, len(date) + 1, asc(date)),
        (37386, 5, 1, [_rat(focal, 10)]),
        (42036, 2, len(lens) + 1, asc(lens)),
    ]

    def ifd0_entries(pix_off: int, exif_off: int):
        return [
            (254, 4, 1, 0), (256, 4, 1, w), (257, 4, 1, h), (258, 3, 1, 16), (259, 3, 1, 1),
            (262, 3, 1, 32803), (271, 2, len(make) + 1, asc(make)), (272, 2, len(model) + 1, asc(model)),
            (273, 4, 1, pix_off), (274, 3, 1, orientation), (277, 3, 1, 1), (278, 4, 1, h),
            (279, 4, 1, len(data)), (34665, 4, 1, exif_off),
            (33421, 3, 2, [2, 2]), (33422, 1, 4, bytes([0, 1, 1, 2])),
            (50706, 1, 4, bytes([1, 4, 0, 0])), (50714, 3, 1, black), (50717, 3, 1, white),
            (50721, 10, 9, [(int(round(v * 10000)), 10000) for v in XYZ_TO_CAM.ravel()]),
            (50728, 5, 3, [(int(round(v * 10000)), 10000) for v in neutral]),
            (50778, 3, 1, 21),
        ]

    def assemble(pix_off: int) -> bytes:
        n0 = len(ifd0_entries(0, 0))
        exif_off = 8 + _ifd_size(n0)
        extra_base = exif_off + _ifd_size(len(exif_entries))
        extra = bytearray()
        i0 = _build_ifd(ifd0_entries(pix_off, exif_off), extra_base, extra)
        ie = _build_ifd(exif_entries, extra_base, extra)
        return b"II*\0" + struct.pack("<I", 8) + i0 + ie + bytes(extra)

    pix_off = len(assemble(0))
    pix_off += pix_off % 2
    body = assemble(pix_off).ljust(pix_off, b"\0")
    Path(path).write_bytes(body + data)


def planck_xyz(cct: float) -> np.ndarray:
    """Approximate XYZ (Y=1) of a daylight/blackbody illuminant (Kim et al. cubic, 3000-25000 K)."""
    T = float(cct)
    if T < 7000:
        x = -4.6070e9 / T**3 + 2.9678e6 / T**2 + 0.09911e3 / T + 0.244063
    else:
        x = -2.0064e9 / T**3 + 1.9018e6 / T**2 + 0.24748e3 / T + 0.237040
    if T < 4000:   # blackbody branch (Kim): good enough for tungsten-like tests
        x = -0.2661239e9 / T**3 - 0.2343589e6 / T**2 + 0.8776956e3 / T + 0.179910
    y = -3.0 * x * x + 2.87 * x - 0.275
    return np.array([x / y, 1.0, (1 - x - y) / y])


def neutral_for_cct(cct: float) -> tuple[float, float, float]:
    """Camera response (G=1) of an illuminant of the given CCT, i.e. DNG AsShotNeutral."""
    n = XYZ_TO_CAM @ planck_xyz(cct) / (XYZ_TO_CAM @ planck_xyz(6504))
    return tuple(float(v / n[1]) for v in n)


def mosaic(rgb_lin: np.ndarray, neutral=(0.5, 1.0, 0.65), black=256, white=16383,
           iso_noise=0.0, rng=None) -> np.ndarray:
    """Linear-sRGB scene (HxWx3, 1.0=diffuse white*headroom) → RGGB Bayer 16-bit counts."""
    h, w, _ = rgb_lin.shape
    cam = rgb_lin @ SRGB_TO_CAM.T                      # scene in camera space (illuminant-neutral)
    cam = cam * np.asarray(neutral)                    # sensor sees illuminant: neutral objects → neutral
    cam = np.clip(cam, 0, None)
    if iso_noise and rng is not None:
        cam = cam + rng.normal(0, iso_noise, cam.shape) * np.sqrt(np.clip(cam, 1e-4, None))
    cam = np.clip(cam, 0, 1)
    bay = np.zeros((h, w))
    bay[0::2, 0::2] = cam[0::2, 0::2, 0]
    bay[0::2, 1::2] = cam[0::2, 1::2, 1]
    bay[1::2, 0::2] = cam[1::2, 0::2, 1]
    bay[1::2, 1::2] = cam[1::2, 1::2, 2]
    return np.clip(black + bay * (white - black), 0, white).astype(np.uint16)


def _srgb_decode(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def scene(kind: str, w=480, h=320, seed=0) -> tuple[np.ndarray, dict]:
    """Return (linear sRGB scene, parameters for mosaic/write)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    u, v = xx / w, yy / h
    base = 0.30 + 0.45 * (1 - v)[..., None] * np.ones(3)          # neutral grey wall, gentle gradient
    img = _srgb_decode(base)
    meta = dict(data_cct=5200, shot_cct=5200, iso=200)

    def blob(cx, cy, rx, ry, rgb_srgb, soft=0.15):
        d = ((u - cx) / rx) ** 2 + ((v - cy) / ry) ** 2
        m = np.clip((1 - d) / soft, 0, 1)[..., None]
        return m, _srgb_decode(np.array(rgb_srgb) / 255.0)

    def paint(m, col):
        nonlocal img
        img = img * (1 - m) + col * m

    # Portrait: head (skin), hair, shirt, patches of green/blue/red for HSL, gray card.
    m, c = blob(0.5, 0.40, 0.14, 0.25, (224, 172, 140)); paint(m, c)   # skin
    m, c = blob(0.5, 0.20, 0.16, 0.12, (60, 40, 30)); paint(m, c)      # hair
    m, c = blob(0.5, 0.88, 0.28, 0.18, (70, 90, 150)); paint(m, c)     # shirt (blue)
    m, c = blob(0.15, 0.7, 0.10, 0.16, (60, 140, 60)); paint(m, c)     # foliage
    m, c = blob(0.85, 0.7, 0.10, 0.16, (170, 60, 50)); paint(m, c)     # red object
    m, c = blob(0.85, 0.2, 0.07, 0.10, (200, 200, 200)); paint(m, c)   # gray card

    if kind == "normal":
        pass
    elif kind == "underexposed":
        img = img * 0.18
    elif kind == "overexposed":
        img = np.clip(img * 4.0, 0, 1.0)
    elif kind == "high_contrast":
        img = np.clip((img - 0.2) * 2.6 + 0.15, 0, 1) * np.where(u[..., None] > 0.5, 1.0, 0.25)
    elif kind == "low_contrast":
        img = 0.18 + 0.22 * img
    elif kind == "warm_cast":
        meta["data_cct"] = 3600          # scene really lit by warm light; camera WB stayed at 5200 K
    elif kind == "cool_cast":
        meta["data_cct"] = 8200
    elif kind == "no_skin":
        img = _srgb_decode(np.stack([0.25 + 0.4 * u, 0.35 + 0.3 * v, 0.5 + 0.1 * u], -1))
    elif kind == "high_iso":
        meta["iso"] = 6400
    else:
        raise ValueError(kind)
    return np.clip(img, 0, 1.0), meta


KINDS = ["normal", "underexposed", "overexposed", "high_contrast", "low_contrast",
         "warm_cast", "cool_cast", "no_skin", "high_iso"]


def generate_set(out_dir: Path, ext: str = ".CR2", kinds=KINDS, w=480, h=320) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, k in enumerate(kinds):
        rgb, meta = scene(k, w, h, seed=i)
        rng = np.random.default_rng(100 + i)
        noise = 0.06 if meta["iso"] >= 3200 else 0.0015
        bay = mosaic(rgb, neutral=neutral_for_cct(meta["data_cct"]), iso_noise=noise, rng=rng)
        p = out_dir / f"IMG_{i + 1:04d}_{k}{ext}"
        write_dng(p, bay, neutral=neutral_for_cct(meta["shot_cct"]), iso=meta["iso"])
        paths.append(p)
    return paths


def generate_jpeg_set(out_dir: Path, kinds=KINDS, w=480, h=320) -> list[Path]:
    """Camera-like JPEGs (sRGB, Exif with make/model/ISO/aperture/shutter) of the same synthetic scenes."""
    from PIL import Image
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, k in enumerate(kinds):
        rgb, meta = scene(k, w, h, seed=i)
        enc = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * np.power(np.clip(rgb, 1e-9, 1), 1 / 2.4) - 0.055)
        img = Image.fromarray((np.clip(enc, 0, 1) * 255 + 0.5).astype("uint8"))
        ex = Image.Exif()
        ex[0x010F], ex[0x0110] = "Canon", "Canon EOS SYNTH"
        ifd = ex.get_ifd(0x8769)
        ifd[0x8827], ifd[0x829D], ifd[0x829A], ifd[0x920A] = meta["iso"], 2.8, 0.005, 50.0
        p = out_dir / f"IMG_{i + 1:04d}_{k}.JPG"
        img.save(p, "JPEG", quality=92, exif=ex)
        paths.append(p)
    return paths


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--ext", default=".CR2")
    a = ap.parse_args()
    for p in generate_set(a.out_dir, a.ext):
        print(p)
