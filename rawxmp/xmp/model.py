"""XMPModel: in-memory representation of a Camera Raw sidecar (no XML here)."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Iterable

from .schema import CURVES, NS, ORDER, SPECS, format_value


class XMPModelError(ValueError):
    pass


@dataclass
class XMPModel:
    """crs attributes (strings already formatted), curve sequences and preserved extras."""
    attrs: "OrderedDict[str, str]" = field(default_factory=OrderedDict)        # crs:<name> → text
    curves: "OrderedDict[str, list[tuple[int, int]]]" = field(default_factory=OrderedDict)
    namespaces: dict[str, str] = field(default_factory=lambda: dict(NS))
    other_attrs: "OrderedDict[str, str]" = field(default_factory=OrderedDict)  # "prefix:name" non-crs attributes
    extra_xml: list[str] = field(default_factory=list)                        # unknown child elements (raw)
    xmptk: str = "Adobe XMP Core 7.0-c000"
    form: str = "attributes"                      # "attributes" (<Description crs:X="1">) | "elements" (<crs:X>1</crs:X>)

    # ---- mutation ----------------------------------------------------------------------
    def set(self, name: str, value) -> None:
        spec = SPECS.get(name)
        if spec is None:
            raise XMPModelError(f"parâmetro desconhecido: crs:{name}")
        if spec.fmt not in ("str", "bool"):
            try:
                float(value)
            except (TypeError, ValueError) as exc:
                raise XMPModelError(f"valor não numérico para {name}: {value!r}") from exc
        self.attrs[name] = format_value(spec, value)

    def set_many(self, params: dict) -> None:
        for k, v in params.items():
            self.set(k, v)

    def set_curve(self, name: str, points: Iterable[tuple[int, int]]) -> None:
        if name not in CURVES:
            raise XMPModelError(f"curva desconhecida: {name}")
        pts = [(int(x), int(y)) for x, y in points]
        self.curves[name] = pts

    def get(self, name: str, default=None):
        return self.attrs.get(name, default)

    def get_float(self, name: str, default: float | None = None) -> float | None:
        v = self.attrs.get(name)
        try:
            return float(v) if v is not None else default
        except ValueError:
            return default

    def ordered_attrs(self) -> list[tuple[str, str]]:
        known = [(k, self.attrs[k]) for k in ORDER if k in self.attrs]
        extra = [(k, v) for k, v in self.attrs.items() if k not in SPECS]
        return known + extra

    def copy(self) -> "XMPModel":
        return XMPModel(OrderedDict(self.attrs), OrderedDict((k, list(v)) for k, v in self.curves.items()),
                        dict(self.namespaces), OrderedDict(self.other_attrs), list(self.extra_xml), self.xmptk, self.form)
