"""Decision → XMPModel (optionally layered over a template XMP that is preserved)."""
from __future__ import annotations

from .model import XMPModel
from .schema import PER_IMAGE


def build_model(params: dict, curves: dict, cfg, template: XMPModel | None = None) -> XMPModel:
    """Template fields (unknown attributes, other namespaces, extra elements) are kept; computed values win."""
    model = template.copy() if template is not None else XMPModel()
    for k in PER_IMAGE:                      # never inherit another photo's white balance from a template
        model.attrs.pop(k, None)
    params = dict(params)
    if template is not None:                 # keep the template's own Version/ProcessVersion strings
        for k in ("Version", "ProcessVersion"):
            if k in template.attrs:
                params.pop(k, None)
    model.set_many(params)
    for name, pts in curves.items():
        model.set_curve(name, pts)
    tool = cfg.get("xmp.creator_tool")
    if tool and "xmp:CreatorTool" not in model.other_attrs:
        model.other_attrs["xmp:CreatorTool"] = tool
    return model
