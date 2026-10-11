"""Decision → XMPModel (optionally layered over a template XMP that is preserved)."""
from __future__ import annotations

from .model import XMPModel


def build_model(params: dict, curves: dict, cfg, template: XMPModel | None = None) -> XMPModel:
    """Template fields (unknown attributes, other namespaces, extra elements) are kept; computed values win."""
    model = template.copy() if template is not None else XMPModel()
    model.set_many(params)
    for name, pts in curves.items():
        model.set_curve(name, pts)
    tool = cfg.get("xmp.creator_tool")
    if tool and "xmp:CreatorTool" not in model.other_attrs:
        model.other_attrs["xmp:CreatorTool"] = tool
    return model
