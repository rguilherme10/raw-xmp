"""RAW file discovery and output-path resolution."""
from __future__ import annotations

import os
from pathlib import Path


def find_raws(root: str | Path, recursive: bool, extensions: list[str]) -> list[Path]:
    root = Path(root)
    exts = {e.lower() for e in extensions}
    if root.is_file():
        return [root] if root.suffix.lower() in exts else []
    out: list[Path] = []
    if recursive:
        for dp, dn, fn in os.walk(root):
            dn.sort()
            out += [Path(dp) / f for f in sorted(fn) if Path(f).suffix.lower() in exts]
    else:
        out = [p for p in sorted(root.iterdir()) if p.is_file() and p.suffix.lower() in exts]
    return out


def existing_sidecar(raw: Path, out_dir: Path | None, root: Path, xmp_ext: str) -> Path | None:
    """Existing XMP for this RAW (case-insensitive extension match), or None."""
    target = sidecar_path(raw, out_dir, root, xmp_ext)
    d = target.parent
    if not d.exists():
        return None
    stem = target.stem.lower()
    for f in d.iterdir():
        if f.is_file() and f.suffix.lower() == xmp_ext.lower() and f.stem.lower() == stem:
            return f
    return None


def sidecar_path(raw: Path, out_dir: Path | None, root: Path, xmp_ext: str, suffix: str | None = None) -> Path:
    """<raw dir>/<stem>.xmp, or mirrored under out_dir preserving the relative structure."""
    suffix = suffix or xmp_ext
    if out_dir is None:
        return raw.with_name(raw.stem + suffix)
    base = root if root.is_dir() else root.parent
    try:
        rel = raw.relative_to(base)
    except ValueError:
        rel = Path(raw.name)
    return (Path(out_dir) / rel).with_name(raw.stem + suffix)
