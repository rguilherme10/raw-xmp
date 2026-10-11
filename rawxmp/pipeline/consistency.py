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
