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
           "Vibrance", "Saturation", "Temperature", "Tint", "LuminanceSmoothing", "ColorNoiseReduction", "Sharpness"]
FEATURES = ["p0.1", "p1", "p5", "p50", "p95", "p99", "p99.9"]


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def calibrate(folder: str | Path, cfg: Config, recursive: bool = False, out_dir: str | Path = "output/calibration") -> dict:
    # The anchor must be fitted against the BASE algorithm, otherwise a style that already enables it would be fitted twice.
    ha = {**cfg.data["exposure"].get("highlight_anchor", {}), "enabled": False}
    cfg_base = Config({**cfg.data, "exposure": {**cfg.data["exposure"], "highlight_anchor": ha}}, cfg.style_name, cfg.camera_profile)
    rows, feats = [], []
    for raw in find_raws(folder, recursive, cfg.get("files.raw_extensions", [".cr2"])):
        human = next((f for f in raw.parent.iterdir()
                      if f.suffix.lower() == ".xmp" and f.stem.lower() == raw.stem.lower()), None)
        if human is None:
            LOGGER.warning(f"{raw.name}: sem XMP humano ao lado — ignorado na calibração")
            continue
        try:
            dec = analyze_raw(raw, cfg_base)
            hm = parse_xmp(human)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"{raw.name}: {exc}")
            continue
        ex_cfg = cfg.section("exposure")
        from ..exposure.estimator import skin_weight
        w_skin = skin_weight(dec.skin.confidence, ex_cfg) if dec.skin.detected else 0.0
        row = {"file": raw.name, "camera": dec.info.camera, "iso": dec.info.iso, "skin_conf": round(dec.skin.confidence, 3),
               "skin_y": round(dec.skin.median_y or 0.0, 4), "y_p99": round(dec.stats.pct_y["p99"], 5), "skin_led": int(w_skin >= 0.5), "scene_key": dec.stats.key,
               "noise_rel": (dec.estimates.get("detail_noise_relative").value if dec.estimates.get("detail_noise_relative") else None),
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
    gains, limit_sugg, warns = tonal_gains(rows, cfg)
    if gains:
        summary["estimate_gain"] = gains
        for k, v in gains.items():
            summary["suggestions"][f"exposure.estimate_gain.{k}"] = v
    summary["suggestions_limits"] = limit_sugg
    summary.setdefault("warnings", []).extend(warns)
    anchor = fit_highlight_anchor(rows, cfg)
    if anchor:
        summary["highlight_anchor"] = anchor
        if anchor["recommended"]:
            for k in ("slope", "intercept", "weight"):
                summary["suggestions"][f"exposure.highlight_anchor.{k}"] = anchor[k]
            summary["suggestions"]["exposure.highlight_anchor.enabled"] = True
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


def fit_highlight_anchor(rows: list[dict], cfg: Config, min_n: int = 20, min_gain: float = 0.02) -> dict | None:
    """Fit  exposure ~ intercept + slope*log2(p99)  and validate leave-one-out against the base algorithm.

    The blend weight follows the estimator: it shrinks as the skin estimate gains confidence.  Returns None without
    enough individually edited pairs; ``recommended`` only when the validated gain is >= ``min_gain`` EV.
    """
    import numpy as np
    ex = cfg.section("exposure")
    vals = [r.get("Exposure2012_human") for r in rows if r.get("Exposure2012_human") is not None]
    cnt = Counter(round(v, 3) for v in vals)
    mode, mode_n = cnt.most_common(1)[0] if cnt else (None, 0)
    preset = mode_n >= 3 and mode_n / max(len(vals), 1) >= 0.4
    use = [r for r in rows if r.get("Exposure2012_human") is not None and r.get("Exposure2012_algo") is not None
           and r.get("y_p99") and not (preset and round(r["Exposure2012_human"], 3) == mode)]
    if len(use) < min_n:
        return None
    x = np.log2(np.maximum([r["y_p99"] for r in use], 1e-4))
    h = np.array([r["Exposure2012_human"] for r in use], float)
    a = np.array([r["Exposure2012_algo"] for r in use], float)
    conf = np.array([r.get("skin_conf") or 0.0 for r in use], float)
    lo, hi = ex["skin_min_confidence"], ex["skin_full_confidence"]
    w_skin = np.clip((conf - lo) / max(hi - lo, 1e-6), 0, 1)
    weight = 0.9
    pred = np.zeros(len(use))
    for i in range(len(use)):
        m = np.ones(len(use), bool)
        m[i] = False
        coef = np.linalg.lstsq(np.c_[np.ones(m.sum()), x[m]], h[m], rcond=None)[0]
        pred[i] = coef[0] + coef[1] * x[i]
    blend = (1 - weight * (1 - w_skin)) * a + weight * (1 - w_skin) * pred
    coef = np.linalg.lstsq(np.c_[np.ones(len(use)), x], h, rcond=None)[0]
    mae0, mae1 = float(np.abs(a - h).mean()), float(np.abs(blend - h).mean())
    return {"n": len(use), "intercept": round(float(coef[0]), 3), "slope": round(float(coef[1]), 3), "weight": weight,
            "mae_without": round(mae0, 3), "mae_with_loo": round(mae1, 3), "recommended": bool(mae0 - mae1 >= min_gain)}


def tonal_gains(rows: list[dict], cfg: Config) -> tuple[dict, dict, list[str]]:
    """Image-driven part of the tonal sliders: how much of it does the photographer follow?  (gain), and which
    configured limits clip the photographer's look (limit suggestions)."""
    gains, limit_sugg, warns = {}, {}, []
    for key, name in (("Contrast2012", "contrast"), ("Highlights2012", "highlights"), ("Shadows2012", "shadows"),
                      ("Whites2012", "whites"), ("Blacks2012", "blacks")):
        ok = [(r.get(f"{key}_algo"), r.get(f"{key}_human")) for r in rows]
        ok = [(x, y) for x, y in ok if x is not None and y is not None]
        if len(ok) < 20:
            continue
        lo, hi = cfg.limit(name)
        at_limit = sum(1 for x, _ in ok if x <= lo + 1e-9 or x >= hi - 1e-9) / len(ok)
        if at_limit >= 0.3:
            hv = [y for _, y in ok]
            limit_sugg[f"limits.{name}"] = {"min": int(min(lo, max(-100, min(hv) - 10))), "max": int(max(hi, min(100, max(hv) + 10)))}
            warns.append(f"{key}: {at_limit * 100:.0f}% das fotos encostam no limite configurado [{lo:g}, {hi:g}] — o limite corta o seu look; "
                         f"sugestão limits.{name} = {limit_sugg[f'limits.{name}']}")
            continue
        bias = cfg.get(f"style.{name}_bias", 0) or 0
        xs, ys = [x - bias for x, _ in ok], [y - bias for _, y in ok]
        sxx = sum(x * x for x in xs)
        if sxx > 1e-9:
            gains[name] = round(min(max(sum(x * y for x, y in zip(xs, ys)) / sxx, 0.0), 1.2), 2)
    return gains, limit_sugg, warns


def write_to_style(name: str, suggestions: dict, profiles_dir: Path | None = None, limits: dict | None = None) -> Path:
    """Merge accepted calibration results into profiles/styles/<name>.yaml (created if missing).

    Accepts dotted keys under ``exposure.`` (targets and ``estimate_gain.<slider>``) and ``limits`` overrides.
    """
    import re
    import yaml
    from ..config import ROOT
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        raise ValueError("nome de estilo inválido")
    dest = (profiles_dir or ROOT / "profiles") / "styles" / f"{name}.yaml"
    doc = (yaml.safe_load(dest.read_text(encoding="utf-8")) if dest.exists() else None) or {}
    changed = {}
    for key, v in suggestions.items():
        if not key.startswith("exposure.") or v is None:
            continue
        parts = key.split(".")[1:]
        cur = doc.setdefault("exposure", {})
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = v
        changed[key] = v
    for lname, lv in (limits or {}).items():
        doc.setdefault("limits", {})[lname.split(".")[-1]] = lv
        changed[lname] = lv
    if not changed:
        raise ValueError("nenhuma sugestão disponível para gravar (precisa de ≥ 5 amostras individuais por grupo ou ≥ 20 pares para ganhos)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return dest
