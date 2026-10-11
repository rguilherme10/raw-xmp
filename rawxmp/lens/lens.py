"""Lens-correction settings.  Profiles are matched by Camera Raw; we only enable/point to them."""
from __future__ import annotations

from ..types import Estimate


def lens_settings(lens_name: str | None, cfg) -> tuple[dict, Estimate]:
    lc = cfg.section("lens")
    if not lc.get("enable_profile", True):
        return {}, Estimate(False, 1.0, "correção de lente desativada na configuração")
    params = {"LensProfileEnable": 1, "LensProfileSetup": "LensDefaults",
              "LensProfileDistortionScale": 100, "LensProfileChromaticAberrationScale": 100,
              "LensProfileVignettingScale": 100}
    if lc.get("auto_lateral_ca", True):
        params["AutoLateralCA"] = 1
    reason = "perfil de lente automático do Camera Raw"
    mapped = (lc.get("profiles") or {}).get(lens_name or "")
    if mapped:
        params["LensProfileName"] = mapped["name"]
        params["LensProfileFilename"] = mapped["filename"]
        reason = f"perfil mapeado em config para a lente '{lens_name}'"
    elif lens_name:
        reason += f" (lente informada: {lens_name}; sem mapeamento explícito)"
    else:
        reason += " (lente não informada pelo EXIF)"
    return params, Estimate(True, 0.6 if lens_name else 0.4, reason)
