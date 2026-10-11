"""Metadata extraction.

Strategy: use ExifTool when installed (most complete, handles Canon MakerNotes lens ids);
otherwise parse the TIFF container directly (CR2 *is* a TIFF: IFD0 + Exif IFD).
The RAW file is only ever opened read-only.
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def _read_ifd(fh, endian: str, offset: int, max_entries: int = 512) -> dict[int, object]:
    fh.seek(offset)
    n = struct.unpack(endian + "H", fh.read(2))[0]
    if n > max_entries:
        raise ValueError("implausible IFD size")
    out: dict[int, object] = {}
    for _ in range(n):
        tag, typ, count = struct.unpack(endian + "HHI", fh.read(8))
        raw = fh.read(4)
        size = _TYPE_SIZE.get(typ, 1) * count
        if size > 1 << 20:
            continue
        if size <= 4:
            data = raw[:size]
        else:
            pos = fh.tell()
            fh.seek(struct.unpack(endian + "I", raw)[0])
            data = fh.read(size)
            fh.seek(pos)
        out[tag] = _decode(endian, typ, count, data)
    return out


def _decode(endian: str, typ: int, count: int, data: bytes):
    try:
        if typ == 2:
            return data.split(b"\0")[0].decode("latin-1").strip()
        if typ in (3, 4, 8, 9):
            fmt = {3: "H", 4: "I", 8: "h", 9: "i"}[typ]
            v = struct.unpack(endian + fmt * count, data)
            return v[0] if count == 1 else v
        if typ in (5, 10):
            fmt = "II" if typ == 5 else "ii"
            v = struct.unpack(endian + fmt * count, data)
            r = [(v[i] / v[i + 1]) if v[i + 1] else 0.0 for i in range(0, len(v), 2)]
            return r[0] if count == 1 else r
        if typ == 1:
            return data[0] if count == 1 else data
    except struct.error:
        return None
    return data


def parse_tiff_exif(path: Path) -> dict:
    """Return normalised metadata from IFD0 + Exif IFD of a TIFF-based raw file."""
    with open(path, "rb") as fh:
        head = fh.read(8)
        if head[:2] == b"II":
            endian = "<"
        elif head[:2] == b"MM":
            endian = ">"
        else:
            raise ValueError("not a TIFF-based file")
        if struct.unpack(endian + "H", head[2:4])[0] != 42:
            raise ValueError("bad TIFF magic")
        ifd0 = _read_ifd(fh, endian, struct.unpack(endian + "I", head[4:8])[0])
        exif: dict = {}
        if isinstance(ifd0.get(34665), int):
            try:
                exif = _read_ifd(fh, endian, ifd0[34665])
            except (ValueError, struct.error, OSError):
                exif = {}
    iso = exif.get(34855) or exif.get(34867)
    if isinstance(iso, tuple):
        iso = iso[0]
    return {
        "make": ifd0.get(271), "model": ifd0.get(272), "orientation": ifd0.get(274),
        "datetime": exif.get(36867) or ifd0.get(306),
        "iso": int(iso) if iso else None,
        "aperture": exif.get(33437), "shutter": exif.get(33434),
        "focal_length": exif.get(37386), "lens": exif.get(42036) or None,
    }


def exiftool_metadata(path: Path) -> dict | None:
    exe = shutil.which("exiftool")
    if not exe:
        return None
    try:
        res = subprocess.run([exe, "-j", "-n", "-Make", "-Model", "-LensModel", "-LensID", "-LensInfo",
                              "-ISO", "-FNumber", "-ExposureTime", "-FocalLength", "-Orientation",
                              "-DateTimeOriginal", str(path)], capture_output=True, timeout=30, check=True)
        d = json.loads(res.stdout)[0]
    except (subprocess.SubprocessError, OSError, ValueError, IndexError):
        return None
    return {
        "make": d.get("Make"), "model": d.get("Model"), "orientation": d.get("Orientation"),
        "datetime": d.get("DateTimeOriginal"), "iso": d.get("ISO"), "aperture": d.get("FNumber"),
        "shutter": d.get("ExposureTime"), "focal_length": d.get("FocalLength"),
        "lens": d.get("LensModel") or d.get("LensID"),
    }


def read_metadata(path: Path) -> tuple[dict, str]:
    """(metadata, source) where source in {'exiftool','tiff','none'}; never raises."""
    meta = exiftool_metadata(path)
    if meta:
        return meta, "exiftool"
    try:
        return parse_tiff_exif(path), "tiff"
    except (ValueError, struct.error, OSError):
        return {}, "none"
