"""Batch-level consistency: pull uncertain white-balance estimates toward the group median.

STYLE keeps the look constant; this keeps the *illuminant interpretation* constant across a
series shot under the same light, without touching confidently-estimated outliers.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path

from ..config import Config


def apply_consistency(packets: list[dict], cfg: Config) -> list[str]:
    cc = cfg.section("consistency")
    notes: list[str] = []
    if not cc.get("enabled", True):
        return notes
    ok = [p for p in packets if p.get("status") == "analyzed" and p["wb"].get("as_shot_kelvin")]
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for p in ok:
        key = (str(Path(p["path"]).parent) if cc.get("scope", "folder") == "folder" else "all", p.get("camera"))
        groups[key].append(p)
    wbc = cfg.section("white_balance")
    ap = wbc["apply_threshold"]
    for key, items in groups.items():
        if len(items) < 3:
            continue
        eff_k = [p["wb"]["kelvin"] for p in items]
        eff_t = [p["wb"]["tint"] for p in items]
        med_k, med_t = statistics.median(eff_k), statistics.median(eff_t)
        members = [p for p in items if abs(p["wb"]["kelvin"] - med_k) <= cc["kelvin_tolerance"]]
        if len(members) < 3:
            continue
        for p in members:
            wb = p["wb"]
            alpha = cc["max_pull"] * max(0.0, min(1.0, (cc["certain_above"] - wb["confidence"]) / cc["certain_above"]))
            if alpha <= 0.01:
                continue
            nk = wb["kelvin"] + alpha * (med_k - wb["kelvin"])
            nt = wb["tint"] + alpha * (med_t - wb["tint"])
            ak, at = wb["as_shot_kelvin"], wb["as_shot_tint"]
            custom = abs(nk - ak) >= ap["kelvin"] or abs(nt - at) >= ap["tint"]
            if abs(nk - wb["kelvin"]) < 1 and abs(nt - wb["tint"]) < 0.2:
                continue
            msg = (f"consistência: WB {wb['kelvin']:.0f} K/{wb['tint']:+.0f} → {nk:.0f} K/{nt:+.0f} "
                   f"(mediana do grupo {med_k:.0f} K/{med_t:+.0f}, n={len(members)}, puxada {alpha:.2f})")
            wb["kelvin"], wb["tint"] = nk, nt
            wb.setdefault("consistency_notes", []).append(msg)
            if custom:
                p["params"]["WhiteBalance"] = "Custom"
                p["params"]["Temperature"] = round(nk)
                p["params"]["Tint"] = round(nt)
            elif p["params"].get("WhiteBalance") == "Custom":     # pulled back to the camera value
                p["params"]["WhiteBalance"] = "As Shot"
                p["params"].pop("Temperature", None)
                p["params"].pop("Tint", None)
            notes.append(f"{Path(p['path']).name}: {msg}")
    return notes


def _settings_key(meta: dict):
    """Camera settings that define 'the same exposure': body, lens, ISO, aperture and shutter (1/3-stop buckets)."""
    import math
    ap, sh, iso = meta.get("aperture"), meta.get("shutter"), meta.get("iso")
    if not ap or not sh or not iso:
        return None
    return (meta.get("camera"), meta.get("lens"), iso, round(math.log2(ap) * 6), round(math.log2(sh) * 3))


def consistent_exposures(items: list[dict], cc: dict) -> dict[str, float]:
    """items: [{path, meta, exposure}] -> {path: shared exposure}.  Pure function (used by batch and calibrate).

    Frames shot with identical settings in the same folder differ in brightness mainly because of subject/background,
    not because the exposure should differ.  Estimates within ``tolerance_ev`` of the group median are pulled toward it.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for it in items:
        k = _settings_key(it["meta"])
        if k is not None:
            groups[(str(Path(it["path"]).parent), k)].append(it)
    out: dict[str, float] = {}
    for g in groups.values():
        if len(g) < cc.get("min_group", 3):
            continue
        med = statistics.median(i["exposure"] for i in g)
        for i in g:
            e = i["exposure"]
            if abs(e - med) <= cc.get("tolerance_ev", 0.8):
                e = e + cc.get("max_pull", 0.7) * (med - e)
            out[i["path"]] = round(round(e / 0.05) * 0.05, 3)
    return out


def apply_exposure_consistency(packets: list[dict], cfg: Config) -> list[str]:
    cc = cfg.section("consistency").get("exposure", {})
    if not cc.get("enabled", False):
        return []
    ok = [p for p in packets if p.get("status") == "analyzed"]
    new = consistent_exposures([{"path": p["path"], "meta": p["report"]["metadata"],
                                 "exposure": p["params"]["Exposure2012"]} for p in ok], cc)
    lo, hi = cfg.limit("exposure")
    notes = []
    for p in ok:
        if p["path"] in new and abs(new[p["path"]] - p["params"]["Exposure2012"]) >= 0.05:
            v = min(max(new[p["path"]], lo), hi)
            notes.append(f"{Path(p['path']).name}: consistência de exposição {p['params']['Exposure2012']:+.2f} → {v:+.2f}")
            p["params"]["Exposure2012"] = v
            p["report"].setdefault("consistency", []).append(notes[-1])
    return notes
