"""Batch processing: discovery → (parallel) analysis → consistency → validation → atomic writes."""
from __future__ import annotations

import json
import os
import shutil
import time
import traceback
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import Config, load_config
from ..correction.engine import Decision, analyze_raw
from ..logging_setup import LOGGER
from ..raw.reader import RawReadError
from ..validation.xmp_validator import validate_model
from ..xmp.builder import build_model
from ..xmp.parser import parse_xmp
from ..xmp.serializer import XMPSerializer
from .consistency import apply_consistency
from .discovery import existing_sidecar, find_raws, sidecar_path


@dataclass
class Options:
    path: str
    style: str | None = None
    config_path: str | None = None
    recursive: bool = False
    overwrite: bool = False
    backup: bool = False
    dry_run: bool = False
    write_xmp: bool = True
    workers: int = 0                       # 0 = auto
    output_dir: str | None = None
    report_dir: str | None = None
    cache_dir: str | None = None
    template: str | None = None
    reference: str | None = None
    write_reports: bool = True


@dataclass
class FileResult:
    path: str
    status: str                            # written | skipped | analyzed | error
    xmp: str | None = None
    report: str | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)       # parameters + estimates (for dry-run display)


def _fmt_signed(x, nd=0):
    return f"{x:+.{nd}f}"


def _log_lines(dec: Decision) -> list[tuple[str, str]]:
    i, P = dec.info, dec.params
    L = [("INFO", f"Processando {i.file_name}"),
         ("INFO", f"Câmera: {i.camera or 'desconhecida'}" + (f" | Lente: {i.lens}" if i.lens else "")),
         ("INFO", f"ISO: {i.iso if i.iso else 'n/d'}"),
         ("INFO", f"Exposure calculada: {_fmt_signed(P['Exposure2012'], 2)} "
                  f"(confiança {dec.estimates['exposure'].confidence:.2f})"),
         ("INFO", f"  Motivo: {dec.estimates['exposure'].reason}"),
         ("INFO", f"  Cena: {dec.stats.key} | Y p50={dec.stats.pct_y['p50']:.3f} p99={dec.stats.pct_y['p99']:.3f} "
                  f"p99.9={dec.stats.pct_y['p99.9']:.3f} | cortado {dec.stats.near_white_fraction * 100:.2f}%"),
         ("INFO", f"Temperature: {round(dec.wb.temperature.value)} ({dec.wb.mode}) | Tint: {_fmt_signed(dec.wb.tint.value)}"),
         ("INFO", f"Skin detected: {str(dec.skin.detected).lower()}")]
    L.append(("INFO", f"  Pele: {dec.skin.reason}"))
    if dec.skin.detected:
        L.append(("INFO", f"Skin confidence: {dec.skin.confidence:.2f}"))
    for w in i.warnings:
        L.append(("WARNING", f"{i.file_name}: {w}"))
    return L


def analyze_worker(path: str, style: str | None, config_path: str | None, cache_dir: str | None) -> dict:
    """Top-level (picklable) function executed in worker processes. Never raises."""
    name = Path(path).name
    t0 = time.perf_counter()
    try:
        cfg = load_config(style, config_path)
        dec = analyze_raw(path, cfg, cache_dir)
        dec.skin.mask = None
        report = dec.to_report(cfg, cfg.get("report.include_histogram", True))
        wb = dec.wb
        eff_k = wb.temperature.value
        return {
            "status": "analyzed", "path": path, "camera": dec.info.camera, "params": dec.params,
            "curves": {k: [list(p) for p in v] for k, v in dec.curves.items()}, "report": report,
            "wb": {"kelvin": eff_k, "tint": wb.tint.value, "confidence": wb.temperature.confidence,
                   "as_shot_kelvin": wb.as_shot_temperature, "as_shot_tint": wb.as_shot_tint, "mode": wb.mode},
            "lines": _log_lines(dec), "seconds": time.perf_counter() - t0,
        }
    except RawReadError as exc:
        return {"status": "error", "path": path, "lines": [("ERROR", str(exc))]}
    except Exception as exc:  # noqa: BLE001 — a single bad file must never stop the batch
        return {"status": "error", "path": path,
                "lines": [("ERROR", f"Não foi possível interpretar {name}: {type(exc).__name__}: {exc}"),
                          ("DEBUG", traceback.format_exc())]}


def _atomic_write(target: Path, text: str, overwrite: bool, backup: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() in (".cr2", ".cr3", ".dng", ".nef", ".arw", ".raf", ".orf", ".rw2"):
        raise ValueError(f"recusado: destino parece um arquivo RAW ({target.name})")
    if target.exists():
        if not overwrite:
            raise FileExistsError(str(target))
        if backup:
            shutil.copy2(target, target.with_name(f"{target.name}.bak-{datetime.now():%Y%m%d-%H%M%S}"))
    tmp = target.with_name(f".{target.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, target)


def run_batch(opts: Options) -> list[FileResult]:
    cfg = load_config(opts.style, opts.config_path)
    root = Path(opts.path)
    if not root.exists():
        LOGGER.error(f"Caminho não encontrado: {root}")
        return []
    files = find_raws(root, opts.recursive, cfg.get("files.raw_extensions", [".cr2"]))
    if not files:
        LOGGER.warning(f"Nenhum arquivo RAW encontrado em {root}" + ("" if opts.recursive else " (use --recursive para subpastas)"))
        return []
    out_dir = Path(opts.output_dir) if opts.output_dir else None
    xmp_ext = cfg.get("files.xmp_extension", ".xmp")
    writing = opts.write_xmp and not opts.dry_run
    template = parse_xmp(opts.template) if opts.template else None
    results: dict[str, FileResult] = {}

    todo: list[Path] = []
    for f in files:
        ex = existing_sidecar(f, out_dir, root, xmp_ext) if writing else None
        if ex is not None and not opts.overwrite:
            LOGGER.info(f"SKIP {f.name}: {ex.name} já existe (use --overwrite)")
            results[str(f)] = FileResult(str(f), "skipped", str(ex), "XMP já existe")
        else:
            todo.append(f)
    LOGGER.info(f"{len(files)} RAW(s) encontrados; {len(todo)} a analisar" +
                (" [dry-run]" if opts.dry_run else ""))

    workers = opts.workers or max(1, min(os.cpu_count() or 1, 8))
    packets: list[dict] = []
    t0 = time.perf_counter()
    if workers == 1 or len(todo) <= 1:
        for f in todo:
            pk = analyze_worker(str(f), opts.style, opts.config_path, opts.cache_dir)
            _emit(pk)
            packets.append(pk)
    else:
        # 'spawn': LibRaw/OpenMP can deadlock after fork(); spawn is also the Windows default
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as ex:
            futs = {ex.submit(analyze_worker, str(f), opts.style, opts.config_path, opts.cache_dir): f for f in todo}
            for fut in as_completed(futs):
                try:
                    pk = fut.result()
                except Exception as exc:  # worker crash (e.g. LibRaw segfault → BrokenProcessPool)
                    pk = {"status": "error", "path": str(futs[fut]),
                          "lines": [("ERROR", f"Não foi possível interpretar {futs[fut].name}: processo falhou: {exc}")]}
                _emit(pk)
                packets.append(pk)
    packets.sort(key=lambda p: p["path"])

    for note in apply_consistency(packets, cfg):
        LOGGER.info(note)

    rep_root = Path(opts.report_dir) if opts.report_dir else (None if writing else Path("output") / "reports")
    for pk in packets:
        raw = Path(pk["path"])
        if pk["status"] == "error":
            results[pk["path"]] = FileResult(pk["path"], "error", message=pk["lines"][0][1])
            continue
        try:
            results[pk["path"]] = _finish(pk, raw, cfg, opts, writing, out_dir, rep_root, root, xmp_ext, template)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(f"Falha ao gravar resultados de {raw.name}: {exc}")
            results[pk["path"]] = FileResult(pk["path"], "error", message=str(exc))
    ordered = [results[str(f)] for f in files if str(f) in results]
    n = {s: sum(r.status == s for r in ordered) for s in ("written", "analyzed", "skipped", "error")}
    LOGGER.info(f"Concluído em {time.perf_counter() - t0:.1f}s: {n['written']} XMP gravado(s), "
                f"{n['analyzed']} apenas analisado(s), {n['skipped']} ignorado(s), {n['error']} erro(s)")
    return ordered


def _emit(pk: dict) -> None:
    for level, msg in pk.get("lines", []):
        LOGGER.log({"INFO": 20, "WARNING": 30, "ERROR": 40, "DEBUG": 10}[level], msg)


def _finish(pk, raw: Path, cfg: Config, opts: Options, writing: bool, out_dir, rep_root, root, xmp_ext, template) -> FileResult:
    P = pk["params"]
    for key, lim in (("Temperature", "temperature"), ("Tint", "tint")):
        if key in P:
            lo, hi = cfg.limit(lim)
            P[key] = round(min(max(P[key], lo), hi))
    model = build_model(P, {k: [tuple(p) for p in v] for k, v in pk["curves"].items()}, cfg, template)
    ref = parse_xmp(opts.reference) if opts.reference else None
    val = validate_model(model, cfg, ref)
    warnings = list(val.warnings)
    if not val.ok:
        # defence in depth: never write an invalid sidecar
        raise ValueError("XMP gerado inválido: " + "; ".join(val.errors))
    report = pk["report"]
    report["parameters"] = {k: v for k, v in P.items()}
    report["validation"] = val.to_dict()
    if pk["wb"].get("consistency_notes"):
        report["white_balance"]["consistency"] = pk["wb"]["consistency_notes"]
    summary = {"parameters": dict(P), "estimates": report["estimates"], "wb": report["white_balance"],
               "skin": {"detected": report["skin"]["detected"], "confidence": report["skin"]["confidence"]},
               "curve": report["tone_curve"]["points"]}
    xmp_path = sidecar_path(raw, out_dir, root, xmp_ext)
    rpath = None
    if opts.write_reports:
        suffix = cfg.get("files.report_suffix", ".analysis.json")
        rpath = (sidecar_path(raw, out_dir, root, xmp_ext, suffix) if rep_root is None
                 else sidecar_path(raw, rep_root, root, xmp_ext, suffix))
        rpath.parent.mkdir(parents=True, exist_ok=True)
        rpath.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    if writing:
        _atomic_write(xmp_path, XMPSerializer.serialize(model), opts.overwrite, opts.backup)
        LOGGER.info(f"XMP criado: {xmp_path}")
        return FileResult(str(raw), "written", xmp=str(xmp_path), report=str(rpath) if rpath else None,
                          message="ok", warnings=warnings, summary=summary)
    return FileResult(str(raw), "analyzed", report=str(rpath) if rpath else None, message="ok",
                      warnings=warnings, summary=summary)


def _json_default(o):
    if hasattr(o, "item"):
        return o.item()
    if hasattr(o, "tolist"):
        return o.tolist()
    raise TypeError(type(o).__name__)
