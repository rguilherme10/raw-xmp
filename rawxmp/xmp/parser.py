"""Read an existing XMP into an XMPModel (used for templates, validation and calibration)."""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

from .model import XMPModel, XMPModelError
from .schema import CRS_NS, CURVES, NS


def _strip_xpacket(text: str) -> str:
    return re.sub(r"<\?xpacket[^>]*\?>", "", text)


def parse_xmp(source: str | Path) -> XMPModel:
    """Parse XMP file path or XML text.  Raises XMPModelError for malformed input."""
    text = Path(source).read_text(encoding="utf-8-sig") if _is_path(source) else str(source)
    text = _strip_xpacket(text)
    ns_map: dict[str, str] = {}
    try:
        for ev, data in ET.iterparse(io.StringIO(text), events=("start-ns",)):
            ns_map[data[0]] = data[1]
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise XMPModelError(f"XML inválido: {exc}") from exc
    inv = {u: p for p, u in ns_map.items()}
    inv.update({u: p for p, u in NS.items() if u not in inv})

    def pname(tag: str) -> tuple[str, str]:
        if tag.startswith("{"):
            uri, local = tag[1:].split("}", 1)
            return inv.get(uri, uri), local
        return "", tag

    desc = None
    for el in root.iter():
        if el.tag.endswith("}Description"):
            desc = el
            break
    if desc is None:
        raise XMPModelError("rdf:Description não encontrado")

    model = XMPModel(namespaces={**NS, **ns_map})
    xmptk = root.attrib.get(f"{{{NS['x']}}}xmptk")
    if xmptk:
        model.xmptk = xmptk
    for key, val in desc.attrib.items():
        prefix, local = pname(key)
        if key == f"{{{NS['rdf']}}}about":
            continue
        if key.startswith("{" + CRS_NS + "}"):
            model.attrs[local] = val
        else:
            model.other_attrs[f"{prefix}:{local}"] = val
    for child in desc:
        prefix, local = pname(child.tag)
        if child.tag == f"{{{CRS_NS}}}{local}" and local in CURVES:
            pts = []
            for li in child.iter(f"{{{NS['rdf']}}}li"):
                try:
                    x, y = (int(float(t)) for t in (li.text or "").split(","))
                except ValueError as exc:
                    raise XMPModelError(f"ponto de curva inválido em {local}: {li.text!r}") from exc
                pts.append((x, y))
            model.curves[local] = pts
        else:
            # unknown element: preserve verbatim (re-serialised with its namespace)
            model.extra_xml.append(ET.tostring(child, encoding="unicode"))
    return model


def _is_path(source) -> bool:
    if isinstance(source, Path):
        return True
    s = str(source)
    return "<" not in s and len(s) < 4096 and Path(s).exists()
