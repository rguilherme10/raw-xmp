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
        ex_cfg = cfg.section("exposure")
        w_skin = ex_cfg["skin_weight"] * min(1.0, dec.skin.confidence / ex_cfg["skin_full_confidence"]) if dec.skin.detected else 0.0
        row = {"file": raw.name, "camera": dec.info.camera, "iso": dec.info.iso, "skin_conf": round(dec.skin.confidence, 3),
               "skin_y": round(dec.skin.median_y or 0.0, 4), "skin_led": int(w_skin >= 0.5), "scene_key": dec.stats.key,
               "aperture": dec.info.aperture, "shutter": dec.info.shutter, "lens": dec.info.lens, "dir": str(raw.parent)}
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
    if ex and (ex["n"] == 0 or ex["human_distinct_values"] < 3):
        summary["suggestions"]["note"] = ("Exposição sem variação individual suficiente (repetida/preset): "
                                          "use pares editados foto a foto.")
    elif ex:
        # Exposure is skin-led when a person is detected: that bias belongs to exposure.skin_target_y, the rest to target_median.
        human_exp = Counter(round(r["Exposure2012_human"], 3) for r in rows if r.get("Exposure2012_human") is not None)
        preset_v = human_exp.most_common(1)[0][0] if human_exp and human_exp.most_common(1)[0][1] >= 3 and \
            human_exp.most_common(1)[0][1] / max(sum(human_exp.values()), 1) >= 0.4 else None
        groups = {"skin_led": [], "no_skin": []}
        for r in rows:
            h, a = r.get("Exposure2012_human"), r.get("Exposure2012_algo")
            if h is None or a is None or (preset_v is not None and round(h, 3) == preset_v):
                continue
            groups["skin_led" if r["skin_led"] else "no_skin"].append(a - h)
        summary["exposure_groups"] = {k: {"n": len(v), "bias": round(sum(v) / len(v), 3) if v else None,
                                          "mae": round(sum(abs(x) for x in v) / len(v), 3) if v else None}
                                      for k, v in groups.items()}
        notes = []
        for key, param in (("skin_led", "exposure.skin_target_y"), ("no_skin", "exposure.target_median")):
            v = groups[key]
            if len(v) >= 5:
                summary["suggestions"][param] = round(cfg.get(param) * 2 ** (-sum(v) / len(v)), 4)
            else:
                notes.append(f"{param}: só {len(v)} amostra(s) ({'com' if key == 'skin_led' else 'sem'} pele); precisa de ≥ 5")
        summary["suggestions"]["note"] = ("Viés das fotos COM pele → exposure.skin_target_y; SEM pele → exposure.target_median "
                                          "(preferencialmente em profiles/cameras/*.yaml). " + "; ".join(notes))
    # Would sharing one exposure inside a series (same camera settings) have matched the human better?
    from ..pipeline.consistency import consistent_exposures
    cc = dict(cfg.section("consistency").get("exposure", {}))
    items = [{"path": str(Path(r["dir"]) / r["file"]), "exposure": r["Exposure2012_algo"],
              "meta": {"camera": r["camera"], "lens": r["lens"], "iso": r["iso"], "aperture": r["aperture"], "shutter": r["shutter"]}}
             for r in rows if r.get("Exposure2012_algo") is not None]
    shared = consistent_exposures(items, cc)
    pairs_all = [(r["Exposure2012_algo"], shared.get(str(Path(r["dir"]) / r["file"]), r["Exposure2012_algo"]), r["Exposure2012_human"])
                 for r in rows if r.get("Exposure2012_algo") is not None and r.get("Exposure2012_human") is not None]
    if pairs_all:
        summary["exposure_consistency"] = {
            "n": len(pairs_all), "grouped": sum(1 for r in rows if str(Path(r["dir"]) / r["file"]) in shared),
            "mae_without": round(sum(abs(a - h) for a, _, h in pairs_all) / len(pairs_all), 3),
            "mae_with": round(sum(abs(b - h) for _, b, h in pairs_all) / len(pairs_all), 3),
            "note": "todas as fotos, inclusive as de exposição repetida (a sincronização faz parte do que se quer reproduzir)"}
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


def write_to_style(name: str, suggestions: dict, profiles_dir: Path | None = None) -> Path:
    """Merge accepted exposure suggestions into profiles/styles/<name>.yaml (created if missing)."""
    import re
    import yaml
    from ..config import ROOT
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise ValueError("nome de estilo inválido")
    dest = (profiles_dir or ROOT / "profiles") / "styles" / f"{name}.yaml"
    doc = yaml.safe_load(dest.read_text(encoding="utf-8")) if dest.exists() else {}
    doc = doc or {}
    ex = doc.setdefault("exposure", {})
    changed = {}
    for k in ("skin_target_y", "target_median"):
        v = suggestions.get(f"exposure.{k}")
        if v is not None:
            ex[k] = v
            changed[k] = v
    if not changed:
        raise ValueError("nenhuma sugestão de exposição disponível (precisa de ≥ 5 amostras individuais por grupo)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return dest
