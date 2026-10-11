"""Compare algorithm output against human-made XMPs to calibrate the deterministic rules.

Input: a folder with pairs  NAME.CR2 + NAME.xmp  (the .xmp being the edit you consider good).
Output: per-parameter error statistics, suggested configuration offsets and a features CSV that
could feed a future (optional) learning phase.  Nothing here changes the configuration by itself.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from ..config import Config
from ..correction.engine import analyze_raw
from ..logging_setup import LOGGER
from ..xmp.parser import parse_xmp
from ..xmp.schema import SPECS
from ..pipeline.discovery import find_raws

COMPARE = ["Exposure2012", "Contrast2012", "Highlights2012", "Shadows2012", "Whites2012", "Blacks2012",
           "Vibrance", "Saturation", "Temperature", "Tint"]
FEATURES = ["p0.1", "p1", "p5", "p50", "p95", "p99", "p99.9"]


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def calibrate(folder: str | Path, cfg: Config, recursive: bool = False, out_dir: str | Path = "output/calibration") -> dict:
    rows, feats = [], []
    for raw in find_raws(folder, recursive, cfg.get("files.raw_extensions", [".cr2"])):
        human = next((f for f in raw.parent.iterdir()
                      if f.suffix.lower() == ".xmp" and f.stem.lower() == raw.stem.lower()), None)
        if human is None:
            LOGGER.warning(f"{raw.name}: sem XMP humano ao lado — ignorado na calibração")
            continue
        try:
            dec = analyze_raw(raw, cfg)
            hm = parse_xmp(human)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"{raw.name}: {exc}")
            continue
        row = {"file": raw.name, "camera": dec.info.camera, "iso": dec.info.iso}
        for k in COMPARE:
            mine = dec.params.get(k)
            if k in ("Temperature", "Tint") and mine is None:       # computed As Shot → no absolute value
                mine = dec.wb.temperature.value if k == "Temperature" else dec.wb.tint.value
            theirs = _num(hm.attrs.get(k))
            row[f"{k}_algo"], row[f"{k}_human"] = mine, theirs
        for color in ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"):
            for pre in ("Hue", "Saturation", "Luminance"):
                n = f"{pre}Adjustment{color}"
                row[f"{n}_algo"], row[f"{n}_human"] = dec.params.get(n), _num(hm.attrs.get(n))
        rows.append(row)
        feats.append({"file": raw.name, "iso": dec.info.iso, "key": dec.stats.key, "skin": dec.skin.confidence,
                      **{f"y_{k}": dec.stats.pct_y[k] for k in FEATURES},
                      **{k: row[f"{k}_human"] for k in COMPARE}})
    summary = {"n_pairs": len(rows), "parameters": {}, "suggestions": {}}
    keys = COMPARE + [f"{p}Adjustment{c}" for p in ("Hue", "Saturation", "Luminance")
                      for c in ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta")]
    for k in keys:
        pairs = [(r[f"{k}_algo"], r[f"{k}_human"]) for r in rows
                 if r.get(f"{k}_algo") is not None and r.get(f"{k}_human") is not None]
        if not pairs:
            continue
        errs = [a - h for a, h in pairs]
        n = len(errs)
        bias = sum(errs) / n
        mae = sum(abs(e) for e in errs) / n
        rmse = math.sqrt(sum(e * e for e in errs) / n)
        summary["parameters"][k] = {"n": n, "bias": round(bias, 3), "mae": round(mae, 3), "rmse": round(rmse, 3),
                                    "corr": _corr([a for a, _ in pairs], [h for _, h in pairs])}
    ex = summary["parameters"].get("Exposure2012")
    if ex and ex["n"] >= 5:
        t = cfg.get("exposure.target_median")
        summary["suggestions"]["exposure.target_median"] = round(t * 2 ** (-ex["bias"]), 4)
        summary["suggestions"]["note"] = ("O viés de exposição é absorvido em exposure.target_median "
                                          "(preferencialmente por câmera em profiles/cameras/*.yaml).")
    elif ex:
        summary["suggestions"]["note"] = "Menos de 5 pares: viés de exposição ainda não é confiável."
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "calibration_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    if rows:
        with open(out / "calibration_rows.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        with open(out / "calibration_features.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(feats[0].keys()))
            w.writeheader()
            w.writerows(feats)
    summary["output_dir"] = str(out)
    return summary


def _corr(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb), 3)
