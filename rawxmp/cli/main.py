"""Command line interface:  python main.py <analyze|process|validate|calibrate|compare|styles> ..."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import list_styles, load_config
from ..logging_setup import LOGGER, configure
from ..pipeline.batch import FileResult, Options, run_batch
from ..validation.xmp_validator import validate_file


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", help="pasta com .CR2 (ou um único arquivo)")
    p.add_argument("--style", help="perfil de estilo (profiles/styles/*.yaml). Padrão: base")
    p.add_argument("--config", help="YAML adicional que sobrescreve config/default.yaml")
    p.add_argument("--recursive", "-r", action="store_true", help="processa subpastas")
    p.add_argument("--workers", type=int, default=0, help="processos paralelos (0 = automático)")
    p.add_argument("--output-dir", help="grava XMPs aqui, preservando a estrutura de pastas (padrão: ao lado do RAW)")
    p.add_argument("--report-dir", help="onde gravar os .analysis.json (padrão: ao lado do XMP; em dry-run: output/reports)")
    p.add_argument("--cache-dir", help="cache das renderizações de análise")
    p.add_argument("--template", help="XMP base cujos campos são preservados (os calculados o sobrescrevem)")
    p.add_argument("--reference", help="XMP de referência para checar compatibilidade estrutural")
    p.add_argument("--no-reports", action="store_true", help="não gravar .analysis.json")
    p.add_argument("--json", action="store_true", help="imprime o resumo em JSON no final")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--log-file")


def _opts(a, *, write: bool) -> Options:
    return Options(path=a.path, style=a.style, config_path=a.config, recursive=a.recursive,
                   overwrite=getattr(a, "overwrite", False), backup=getattr(a, "backup", False),
                   dry_run=getattr(a, "dry_run", False) or not write, write_xmp=write, workers=a.workers,
                   output_dir=a.output_dir, report_dir=a.report_dir, cache_dir=a.cache_dir, template=a.template,
                   reference=a.reference, write_reports=not a.no_reports)


def _print_table(results: list[FileResult]) -> None:
    keys = [("Exposure2012", "Exp"), ("Contrast2012", "Ctr"), ("Highlights2012", "Hi"), ("Shadows2012", "Sh"),
            ("Whites2012", "Wh"), ("Blacks2012", "Bl")]
    for r in results:
        if r.status in ("analyzed", "written") and r.summary:
            P, E = r.summary["parameters"], r.summary["estimates"]
            wb = r.summary["wb"]
            print(f"\n{Path(r.path).name}")
            for k, label in keys:
                est = E.get({"Exposure2012": "exposure", "Contrast2012": "contrast", "Highlights2012": "highlights",
                             "Shadows2012": "shadows", "Whites2012": "whites", "Blacks2012": "blacks"}[k], {})
                print(f"  {label:<4}{P[k]:>+7}  conf {est.get('confidence', 0):.2f}  {est.get('reason', '')[:100]}")
            print(f"  WB   {wb['mode']:<8} {wb['temperature']['value']:.0f} K / tint {wb['tint']['value']:+.0f}  "
                  f"conf {wb['temperature']['confidence']:.2f}")
            print(f"  Pele {'sim' if r.summary['skin']['detected'] else 'não'}  conf {r.summary['skin']['confidence']:.2f}")
            print(f"  Curva {r.summary['curve']}")
            if r.report:
                print(f"  Relatório: {r.report}")


def _check_path(a) -> bool:
    if not Path(a.path).exists():
        LOGGER.error(f"Caminho não encontrado: {a.path}")
        return False
    return True


def cmd_analyze(a) -> int:
    if not _check_path(a):
        return 1
    results = run_batch(_opts(a, write=False))
    _print_table(results)
    if a.json:
        print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2, default=str))
    return 1 if any(r.status == "error" for r in results) and not any(r.status == "analyzed" for r in results) else 0


def cmd_process(a) -> int:
    if not _check_path(a):
        return 1
    results = run_batch(_opts(a, write=not a.dry_run))
    if a.dry_run:
        _print_table(results)
    if a.json:
        print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2, default=str))
    errs = sum(r.status == "error" for r in results)
    return 0 if errs == 0 else (2 if errs < len(results) else 1)


def cmd_validate(a) -> int:
    cfg = load_config(a.style, a.config)
    root = Path(a.path)
    files = [root] if root.is_file() else sorted(root.rglob("*.xmp") if a.recursive else root.glob("*.xmp"))
    if not files:
        LOGGER.error("nenhum .xmp encontrado")
        return 1
    bad = 0
    for f in files:
        rep = validate_file(f, cfg, a.reference, enforce_limits=not a.no_limits)
        status = "OK" if rep.ok else "INVÁLIDO"
        LOGGER.info(f"{f.name}: {status} ({len(rep.errors)} erro(s), {len(rep.warnings)} aviso(s))")
        for e in rep.errors:
            LOGGER.error(f"  {e}")
        for w in rep.warnings:
            LOGGER.warning(f"  {w}")
        if rep.compat:
            LOGGER.info(f"  cobertura da referência: {rep.compat['coverage_of_reference'] * 100:.0f}%")
        bad += not rep.ok
    return 1 if bad else 0


def cmd_calibrate(a) -> int:
    from ..calibration.calibrate import calibrate
    cfg = load_config(a.style, a.config)
    s = calibrate(a.path, cfg, a.recursive, a.out)
    LOGGER.info(f"{s['n_pairs']} par(es) CR2+XMP comparados")
    for k, v in s["parameters"].items():
        print(f"  {k:<34} n={v['n']:<3}/{v['n_total']:<3} viés {v['bias']:+8.3f}  MAE {v['mae']:7.3f}  RMSE {v['rmse']:7.3f}  corr {v['corr']}" + ("  [preset]" if v.get("synced_preset") else ""))
    if s.get("highlight_anchor"):
        h = s["highlight_anchor"]
        print(f"Âncora nas altas luzes (exposição ≈ {h['intercept']:+.2f} {h['slope']:+.2f}·log2(p99); n={h['n']}): erro {h['mae_without']} → "
              f"{h['mae_with_loo']} EV (validação leave-one-out) — " + ("RECOMENDADO (gravado com --write-style)" if h["recommended"] else "ganho insuficiente, não recomendado"))
    if s.get("estimate_gain"):
        print("Ganho da estimativa por foto (1,0 = seguir tudo; ~0 = você mantém o valor do estilo):",
              ", ".join(f"{k} {v}" for k, v in s["estimate_gain"].items()))
    if s.get("exposure_groups"):
        g = s["exposure_groups"]
        print("Exposição por tipo de foto:", ", ".join(f"{k}: n={v['n']} viés {v['bias']} MAE {v['mae']}" for k, v in g.items()))
    if s.get("exposure_consistency"):
        c = s["exposure_consistency"]
        print(f"Consistência de exposição em séries (mesmos ajustes de câmera): {c['grouped']}/{c['n']} fotos agrupadas, "
              f"MAE {c['mae_without']} → {c['mae_with']} EV  (ative com consistency.exposure.enabled: true)")
    if getattr(a, "write_style", None):
        from ..calibration.calibrate import write_to_style
        dest = write_to_style(a.write_style, s["suggestions"], limits=s.get("suggestions_limits"))
        LOGGER.info(f"Alvos de exposição gravados em {dest}")
    if not a.style:
        LOGGER.warning("Sem --style: Contraste/Realces/Sombras/Brancos/Pretos/Vibrance são comparados com o estilo BASE, não com o look "
                       "das suas fotos. Gere um estilo com `make-style` e passe --style para comparar de forma justa.")
    for w in s.get("warnings", []):
        LOGGER.warning(w)
    if s["suggestions"]:
        print("Sugestões:", json.dumps(s["suggestions"], ensure_ascii=False))
    LOGGER.info(f"Detalhes em {s['output_dir']}")
    return 0 if s["n_pairs"] else 1


def cmd_compare(a) -> int:
    from ..preview.compare import make_comparison
    cfg = load_config(a.style, a.config)
    out = make_comparison(a.raw, a.out, cfg, a.xmp)
    LOGGER.info(f"Comparação gravada em {out}")
    return 0


def cmd_diagnose(a) -> int:
    """Print what LibRaw reports for a file and how it is interpreted (send this when WB looks wrong)."""
    import numpy as np
    import rawpy
    from ..raw.reader import RawReader, select_xyz_to_cam
    from ..white_balance.estimator import neutral_to_kelvin_tint
    cfg = load_config(a.style, a.config)
    r = RawReader(a.raw)
    info = r.read_info()
    out = {"camera": info.camera, "lens": info.lens, "iso": info.iso, "black_level": info.black_level,
           "white_level": info.white_level, "camera_wb": info.camera_wb, "daylight_wb": info.daylight_wb,
           "matrix_note": info.matrix_note, "xyz_to_cam_used": info.xyz_to_cam, "warnings": info.warnings}
    with rawpy.imread(str(a.raw)) as raw:
        out["rawpy_rgb_xyz_matrix"] = np.asarray(raw.rgb_xyz_matrix).round(5).tolist()
        out["rawpy_color_matrix"] = np.asarray(raw.color_matrix).round(5).tolist()
        out["rawpy_camera_whitebalance"] = [round(float(x), 5) for x in raw.camera_whitebalance]
        out["rawpy_daylight_whitebalance"] = [round(float(x), 5) for x in raw.daylight_whitebalance]
    if info.xyz_to_cam and info.camera_neutral is not None:
        k, t = neutral_to_kelvin_tint(info.camera_neutral, np.asarray(info.xyz_to_cam), cfg)
        out["as_shot_kelvin"], out["as_shot_tint"] = round(k, 1), round(t, 2)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_skin_debug(a) -> int:
    """Why was (or wasn't) skin found?  Per-criterion pass rates + overlay PNG (green = kept, red = candidate removed)."""
    import cv2
    import numpy as np
    from ..raw.reader import RawReader
    from ..raw.render import render_for_analysis
    from ..skin.robust import detect_skin_robust
    cfg = load_config(a.style, a.config)
    reader = RawReader(a.raw)
    lin = render_for_analysis(reader, cfg.get("analysis.max_dimension", 1024))
    dbg: dict = {}
    res = detect_skin_robust(lin, reader.read_info(), cfg, dbg)
    over = dbg.get("rgb_enc", lin).copy()
    if "mask_all" in dbg:
        over[dbg["mask_all"]] = over[dbg["mask_all"]] * 0.4 + np.array([1, 0, 0]) * 0.6
        if "mask_kept" in dbg:
            k = dbg["mask_kept"]
            over[k] = over[k] * 0.4 + np.array([0, 1, 0]) * 0.6
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), cv2.cvtColor((np.clip(over, 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
    info = {k: v for k, v in dbg.items() if k not in ("mask_all", "mask_kept", "rgb_enc")}
    print(json.dumps({"result": res.to_dict(), "debug": info, "overlay": str(out)}, indent=2, ensure_ascii=False))
    return 0


def cmd_make_style(a) -> int:
    from ..config import ROOT
    from ..style_builder import write_style
    dest, notes = write_style(a.xmps, a.name, Path(a.profiles_dir) if a.profiles_dir else ROOT / "profiles",
                              a.with_template, a.force)
    LOGGER.info(f"Estilo gravado em {dest}")
    for n in notes:
        LOGGER.info(f"  {n}")
    LOGGER.info(f"Use:  python main.py process PASTA --style {a.name}")
    return 0


def cmd_styles(a) -> int:
    print("Estilos disponíveis:", ", ".join(["base"] + list_styles()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rawxmp", description="CR2 → XMP (Camera Raw/Lightroom) determinístico e explicável")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("analyze", help="analisa e mostra parâmetros/confiança; nunca grava XMP")
    _common(p)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("process", help="analisa e grava um .xmp para cada RAW")
    _common(p)
    p.add_argument("--overwrite", action="store_true", help="sobrescreve XMPs existentes (padrão: SKIP)")
    p.add_argument("--backup", action="store_true", help="com --overwrite, guarda cópia .bak-AAAAMMDD-HHMMSS")
    p.add_argument("--dry-run", action="store_true", help="não grava XMP; apenas analisa e gera relatório")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("validate", help="valida XMPs (XML, namespaces, parâmetros, limites, referência)")
    p.add_argument("path")
    p.add_argument("--reference", help="XMP de referência estrutural")
    p.add_argument("--style"), p.add_argument("--config")
    p.add_argument("--recursive", "-r", action="store_true")
    p.add_argument("--no-limits", action="store_true", help="não aplicar os limites do config (útil p/ XMPs humanos)")
    p.add_argument("-v", "--verbose", action="store_true"), p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--log-file")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("calibrate", help="compara a análise com XMPs humanos (pares NOME.CR2 + NOME.xmp)")
    p.add_argument("path")
    p.add_argument("--out", default="output/calibration")
    p.add_argument("--write-style", metavar="NOME", help="grava os alvos de exposição sugeridos em profiles/styles/NOME.yaml")
    p.add_argument("--style"), p.add_argument("--config")
    p.add_argument("--recursive", "-r", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true"), p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--log-file")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("compare", help="imagem antes/depois + histogramas (simulação aproximada)")
    p.add_argument("raw")
    p.add_argument("--xmp", help="usa este XMP em vez dos ajustes calculados")
    p.add_argument("--out", default="output/compare.png")
    p.add_argument("--style"), p.add_argument("--config")
    p.add_argument("-v", "--verbose", action="store_true"), p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--log-file")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("diagnose", help="mostra matriz/WB lidos pelo LibRaw e como foram interpretados")
    p.add_argument("raw")
    p.add_argument("--style"), p.add_argument("--config")
    p.add_argument("-v", "--verbose", action="store_true"), p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--log-file")
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("skin-debug", help="diagnostica a detecção de pele de uma foto (critérios + PNG de sobreposição)")
    p.add_argument("raw")
    p.add_argument("--out", default="output/skin_debug.png")
    p.add_argument("--style"), p.add_argument("--config")
    p.add_argument("-v", "--verbose", action="store_true"), p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--log-file")
    p.set_defaults(func=cmd_skin_debug)

    p = sub.add_parser("make-style", help="cria um estilo (YAML) a partir de um ou mais XMPs seus")
    p.add_argument("xmps", nargs="+", help="XMPs de exemplo (use vários para obter a mediana)")
    p.add_argument("--name", required=True, help="nome do estilo (profiles/styles/NOME.yaml)")
    p.add_argument("--with-template", action="store_true",
                   help="copia o 1º XMP como template estrutural (preserva Look, crop e formato de elementos)")
    p.add_argument("--force", action="store_true", help="sobrescreve estilo existente")
    p.add_argument("--profiles-dir", help=argparse.SUPPRESS)
    p.add_argument("-v", "--verbose", action="store_true"), p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--log-file")
    p.set_defaults(func=cmd_make_style)

    p = sub.add_parser("styles", help="lista os perfis de estilo")
    p.add_argument("-v", "--verbose", action="store_true"), p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--log-file")
    p.set_defaults(func=cmd_styles)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    configure(getattr(a, "verbose", False), getattr(a, "quiet", False), getattr(a, "log_file", None))
    try:
        return a.func(a)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        LOGGER.error(str(exc))
        return 1
    except KeyboardInterrupt:
        LOGGER.error("interrompido")
        return 130


if __name__ == "__main__":
    sys.exit(main())
