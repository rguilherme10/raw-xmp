"""Compare algorithm output against human-made XMPs to calibrate the deterministic rules.

Input: a folder with pairs  NAME.CR2 + NAME.xmp  (the .xmp being the edit you consider good).
Output: per-parameter error statistics, suggested configuration offsets and a features CSV that
could feed a future (optional) learning phase.  Nothing here changes the configuration by itself.
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter
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
        # A value shared by >= 3 photos and >= 40 % of the set is a pasted preset, not a per-photo decision: drop those samples.
        counts = Counter(round(h, 3) for _, h in pairs)
        mode, mode_n = counts.most_common(1)[0]
        n_all = len(pairs)
        preset_rows = mode_n >= 3 and mode_n / n_all >= 0.4
        if preset_rows:
            pairs = [(a, h) for a, h in pairs if round(h, 3) != mode]
        entry = {"n": len(pairs), "n_total": n_all, "excluded_as_preset": (mode_n if preset_rows else 0),
                 "preset_value": mode if preset_rows else None,
                 "human_distinct_values": len({round(h, 3) for _, h in pairs}),
                 "synced_preset": bool(n_all >= 4 and (len(counts) == 1))}
        if pairs:
            errs = [a - h for a, h in pairs]
            n = len(errs)
            entry.update(bias=round(sum(errs) / n, 3), mae=round(sum(abs(e) for e in errs) / n, 3),
                         rmse=round(math.sqrt(sum(e * e for e in errs) / n), 3),
                         corr=_corr([a for a, _ in pairs], [h for _, h in pairs]))
        else:
            entry.update(bias=0.0, mae=0.0, rmse=0.0, corr=None)
        summary["parameters"][k] = entry

    summary["warnings"] = []
    for k in COMPARE:
        e = summary["parameters"].get(k)
        if e and e["excluded_as_preset"]:
            summary["warnings"].append(
                f"{k}: o valor {e['preset_value']:g} se repete em {e['excluded_as_preset']} de {e['n_total']} fotos — "
                "parece preset sincronizado (copiar/colar); essas amostras foram EXCLUÍDAS da calibração "
                f"(restam {e['n']})")
    ex = summary["parameters"].get("Exposure2012")
    if ex and ex["n"] == 0:
        summary["suggestions"]["note"] = ("Exposição idêntica em todas as fotos: sem informação para calibrar. "
                                          "Use pares em que a exposição foi ajustada foto a foto.")
    elif ex and (ex["human_distinct_values"] < 3 or ex["n"] < 5):
        summary["suggestions"]["note"] = (f"Só {ex['n']} amostra(s) de exposição individual ({ex['human_distinct_values']} valores "
                                          "distintos): precisa de ≥ 5 pares editados foto a foto para sugerir um novo alvo.")
    elif ex:
        t = cfg.get("exposure.target_median")
        summary["suggestions"]["exposure.target_median"] = round(t * 2 ** (-ex["bias"]), 4)
        summary["suggestions"]["note"] = ("O viés de exposição é absorvido em exposure.target_median "
                                          "(preferencialmente por câmera em profiles/cameras/*.yaml).")
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


def _const(rows, k):
    vals = {r.get(f"{k}_human") for r in rows if r.get(f"{k}_human") is not None}
    return next(iter(vals)) if len(vals) == 1 else "?"


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
