"""XMPSerializer: XMPModel → well-formed XMP text (via ElementTree, never string concatenation)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .model import XMPModel
from .schema import CURVES, NS

XPACKET_BEGIN = '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
XPACKET_END = '\n<?xpacket end="w"?>'


def _q(prefix: str, local: str, ns: dict[str, str]) -> str:
    return f"{{{ns[prefix]}}}{local}"


def to_element(model: XMPModel) -> ET.Element:
    ns = model.namespaces
    for p, u in ns.items():
        ET.register_namespace(p, u)
    root = ET.Element(_q("x", "xmpmeta", ns), {_q("x", "xmptk", ns): model.xmptk})
    rdf = ET.SubElement(root, _q("rdf", "RDF", ns))
    desc = ET.SubElement(rdf, _q("rdf", "Description", ns), {_q("rdf", "about", ns): ""})
    for key, val in model.other_attrs.items():
        prefix, local = key.split(":", 1)
        desc.set(_q(prefix, local, ns), val)
    for name, val in model.ordered_attrs():
        desc.set(_q("crs", name, ns), val)
    for cname in CURVES:
        pts = model.curves.get(cname)
        if pts is None:
            continue
        el = ET.SubElement(desc, _q("crs", cname, ns))
        seq = ET.SubElement(el, _q("rdf", "Seq", ns))
        for x, y in pts:
            li = ET.SubElement(seq, _q("rdf", "li", ns))
            li.text = f"{x}, {y}"
    for raw in model.extra_xml:
        desc.append(ET.fromstring(raw))
    return root


class XMPSerializer:
    @staticmethod
    def serialize(model: XMPModel) -> str:
        root = to_element(model)
        ET.indent(root, space="   ")
        body = ET.tostring(root, encoding="unicode", xml_declaration=False)
        return XPACKET_BEGIN + _tidy(body, model) + XPACKET_END + "\n"


def _tidy(body: str, model: XMPModel) -> str:
    """Put each rdf:Description attribute on its own line (as Lightroom does) for readability."""
    marker = "<rdf:Description "
    i = body.find(marker)
    if i < 0:
        return body
    j = body.find(">", i)
    head = body[i + len(marker): j]
    self_closing = head.endswith("/")
    if self_closing:
        head = head[:-1]
    import re
    attrs = re.findall(r'[\w:.\-]+="[^"]*"', head)
    pad = "   "
    rebuilt = marker.rstrip() + "\n" + "\n".join(f"{pad}{pad}{a}" for a in attrs) + ("/>" if self_closing else ">")
    return body[:i] + rebuilt + body[j + 1:]
