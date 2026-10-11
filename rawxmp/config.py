"""Layered YAML configuration: default.yaml < base style < style < camera override < user file."""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("RAWXMP_HOME", Path(__file__).resolve().parents[1]))


def _merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class Config:
    """Thin dict wrapper with dotted access:  cfg.get('exposure.max')."""

    def __init__(self, data: dict, style_name: str = "base", camera_profile: str | None = None):
        self.data = data
        self.style_name = style_name
        self.camera_profile = camera_profile

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def section(self, name: str) -> dict:
        return self.data.get(name, {})

    def limit(self, name: str) -> tuple[float, float]:
        s = self.section("limits").get(name)
        if s is None:
            raise KeyError(f"limits.{name} missing in configuration")
        return float(s["min"]), float(s["max"])

    def with_camera(self, model: str | None) -> "Config":
        """Apply the first matching profiles/cameras/*.yaml (match: [substrings, case-insens.])."""
        if not model:
            return self
        for f in sorted((ROOT / "profiles" / "cameras").glob("*.yaml")):
            d = _load(f)
            if any(m.lower() in model.lower() for m in d.get("match", [])):
                body = {k: v for k, v in d.items() if k != "match"}
                return Config(_merge(self.data, body), self.style_name, f.stem)
        return self


def list_styles() -> list[str]:
    return sorted(p.stem for p in (ROOT / "profiles" / "styles").glob("*.yaml"))


def load_config(style: str | None = None, config_path: str | Path | None = None) -> Config:
    data = _load(ROOT / "config" / "default.yaml")
    data = _merge(data, _load(ROOT / "profiles" / "base" / "base.yaml"))
    name = "base"
    if style and style != "base":
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", style):
            raise ValueError(f"invalid style name: {style!r}")
        sp = ROOT / "profiles" / "styles" / f"{style}.yaml"
        if not sp.exists():
            raise FileNotFoundError(f"style '{style}' not found. Available: {', '.join(list_styles())}")
        data = _merge(data, _load(sp))
        name = style
    if config_path:
        data = _merge(data, _load(Path(config_path)))
    return Config(data, name)
