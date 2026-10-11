"""XMP validation: XML, namespaces, supported parameters, ranges, limits, structure vs reference."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..xmp.model import XMPModel, XMPModelError
from ..xmp.parser import parse_xmp
from ..xmp.schema import COLORS, CRS_NS, CURVES, NS, REQUIRED_NS, SPECS, parse_value

# schema name → config limit name
LIMIT_MAP = {
    "Exposure2012": "exposure", "Contrast2012": "contrast", "Highlights2012": "highlights",
    "Shadows2012": "shadows", "Whites2012": "whites", "Blacks2012": "blacks", "Temperature": "temperature",
    "Tint": "tint", "Saturation": "saturation", "Vibrance": "vibrance", "Clarity2012": "clarity",
    "Texture": "texture", "Dehaze": "dehaze", "Sharpness": "sharpness",
    "LuminanceSmoothing": "luminance_nr", "ColorNoiseReduction": "color_nr",
}
for _c in COLORS:
    LIMIT_MAP[f"HueAdjustment{_c}"] = "hsl_hue"
    LIMIT_MAP[f"SaturationAdjustment{_c}"] = "hsl_saturation"
    LIMIT_MAP[f"LuminanceAdjustment{_c}"] = "hsl_luminance"


@dataclass
class ValidationReport:
    path: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    compat: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"path": self.path, "ok": self.ok, "errors": self.errors, "warnings": self.warnings,
                "reference_compatibility": self.compat}


def validate_model(model: XMPModel, cfg: Config | None = None, reference: XMPModel | None = None,
                   enforce_limits: bool = True) -> ValidationReport:
    rep = ValidationReport()
    # --- namespaces ---------------------------------------------------------------------
    for pfx in REQUIRED_NS:
        if pfx not in model.namespaces:
            rep.errors.append(f"namespace obrigatório ausente: {pfx}")
    for pfx, uri in model.namespaces.items():
        if pfx in NS and uri != NS[pfx]:
            rep.errors.append(f"namespace '{pfx}' aponta para {uri!r}, esperado {NS[pfx]!r}")
    if model.namespaces.get("crs") != CRS_NS:
        rep.errors.append("namespace crs inválido")
    # --- scalar parameters --------------------------------------------------------------
    for name, text in model.attrs.items():
        spec = SPECS.get(name)
        if spec is None:
            rep.warnings.append(f"parâmetro não suportado por este gerador: crs:{name}")
            continue
        try:
            val = parse_value(spec, text)
        except ValueError:
            rep.errors.append(f"crs:{name}={text!r} não é um valor válido ({spec.fmt})")
            continue
        if spec.choices and text not in spec.choices:
            rep.errors.append(f"crs:{name}={text!r} fora das opções {list(spec.choices)}")
        if spec.fmt not in ("str", "bool"):
            if (spec.lo is not None and val < spec.lo) or (spec.hi is not None and val > spec.hi):
                rep.errors.append(f"crs:{name}={text} fora do intervalo do formato [{spec.lo}, {spec.hi}]")
            if spec.fmt in ("int", "sint") and float(val) != int(val):
                rep.errors.append(f"crs:{name}={text} deveria ser inteiro")
            lim = LIMIT_MAP.get(name)
            if enforce_limits and cfg is not None and lim:
                lo, hi = cfg.limit(lim)
                if val < lo - 1e-9 or val > hi + 1e-9:
                    rep.errors.append(f"crs:{name}={text} fora dos limites configurados [{lo}, {hi}]")
    # --- HSL completeness ---------------------------------------------------------------
    hsl = [f"{p}Adjustment{c}" for p in ("Hue", "Saturation", "Luminance") for c in COLORS]
    present = [h for h in hsl if h in model.attrs]
    if present and len(present) != len(hsl):
        rep.warnings.append(f"HSL incompleto: {len(present)}/24 campos")
    # --- curves -------------------------------------------------------------------------
    for cname, pts in model.curves.items():
        if cname not in CURVES:
            rep.warnings.append(f"curva desconhecida: {cname}")
        if len(pts) < 2:
            rep.errors.append(f"{cname}: precisa de ≥ 2 pontos")
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        if any(not (0 <= v <= 255) for v in xs + ys):
            rep.errors.append(f"{cname}: pontos fora de 0..255")
        if any(b <= a for a, b in zip(xs, xs[1:])):
            rep.errors.append(f"{cname}: x não estritamente crescente")
        if any(b < a for a, b in zip(ys, ys[1:])):
            rep.errors.append(f"{cname}: curva não monotônica (inversão tonal)")
        if xs[0] != 0 or xs[-1] != 255:
            rep.errors.append(f"{cname}: deve começar em x=0 e terminar em x=255")
    # --- structure vs reference ---------------------------------------------------------
    if reference is not None:
        ref_attrs, my_attrs = set(reference.attrs), set(model.attrs)
        missing = sorted(ref_attrs - my_attrs)
        extra = sorted(my_attrs - ref_attrs)
        ref_curves, my_curves = set(reference.curves), set(model.curves)
        ns_missing = sorted(set(reference.namespaces) - set(model.namespaces) - set(NS))
        common = len(ref_attrs & my_attrs)
        rep.compat = {
            "reference_attributes": len(ref_attrs), "generated_attributes": len(my_attrs),
            "coverage_of_reference": round(common / len(ref_attrs), 3) if ref_attrs else 1.0,
            "missing_from_generated": missing, "only_in_generated": extra,
            "curves_missing": sorted(ref_curves - my_curves), "curves_extra": sorted(my_curves - ref_curves),
            "namespaces_missing": ns_missing,
        }
        if missing:
            rep.warnings.append(f"{len(missing)} parâmetro(s) do XMP de referência ausentes no gerado: {missing[:12]}"
                                + ("…" if len(missing) > 12 else ""))
        if rep.compat["curves_missing"]:
            rep.warnings.append(f"curvas da referência ausentes: {rep.compat['curves_missing']}")
    return rep


def validate_file(path: str | Path, cfg: Config | None = None, reference: str | Path | None = None,
                  enforce_limits: bool = True) -> ValidationReport:
    path = Path(path)
    ref_model = None
    if reference:
        try:
            ref_model = parse_xmp(reference)
        except (XMPModelError, OSError) as exc:
            r = ValidationReport(str(path))
            r.errors.append(f"XMP de referência ilegível: {exc}")
            return r
    try:
        model = parse_xmp(path)
    except (XMPModelError, OSError, UnicodeDecodeError) as exc:
        r = ValidationReport(str(path))
        r.errors.append(str(exc))
        return r
    rep = validate_model(model, cfg, ref_model, enforce_limits)
    rep.path = str(path)
    return rep
