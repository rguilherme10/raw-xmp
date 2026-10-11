import json
import os
import shutil
from pathlib import Path

import pytest

from conftest import md5
from rawxmp.cli.main import main
from rawxmp.logging_setup import configure
from rawxmp.pipeline.batch import Options, run_batch
from rawxmp.pipeline.discovery import find_raws
from rawxmp.validation.xmp_validator import validate_file
from rawxmp.config import load_config


def raws(d: Path):
    return sorted(d.glob("*.CR2"))


def opts(d, **kw):
    kw.setdefault("workers", 1)
    return Options(path=str(d), **kw)


def test_process_writes_one_valid_xmp_per_raw_and_preserves_cr2(work_dir):
    before = {p.name: md5(p) for p in raws(work_dir)}
    res = run_batch(opts(work_dir, style="portrait"))
    assert {r.status for r in res} == {"written"} and len(res) == 9
    cfg = load_config("portrait")
    for p in raws(work_dir):
        x = p.with_suffix(".xmp")
        assert x.exists(), p.name
        assert validate_file(x, cfg).ok
    assert before == {p.name: md5(p) for p in raws(work_dir)}          # CR2 untouched, byte for byte
    assert not list(work_dir.glob(".*.tmp*"))                          # no temp leftovers


def test_existing_xmp_is_skipped_unless_overwrite(work_dir):
    target = work_dir / "IMG_0001_normal.xmp"
    target.write_text("HUMAN EDIT", encoding="utf-8")
    res = run_batch(opts(work_dir))
    st = {Path(r.path).name: r.status for r in res}
    assert st["IMG_0001_normal.CR2"] == "skipped" and target.read_text() == "HUMAN EDIT"
    assert sum(s == "written" for s in st.values()) == 8
    res = run_batch(opts(work_dir, overwrite=True))
    assert target.read_text() != "HUMAN EDIT" and all(r.status == "written" for r in res)


def test_skip_is_case_insensitive_on_extension(work_dir):
    (work_dir / "IMG_0002_underexposed.XMP").write_text("UPPER", encoding="utf-8")
    res = run_batch(opts(work_dir))
    assert {Path(r.path).name: r.status for r in res}["IMG_0002_underexposed.CR2"] == "skipped"


def test_backup_on_overwrite(work_dir):
    target = work_dir / "IMG_0001_normal.xmp"
    target.write_text("HUMAN EDIT", encoding="utf-8")
    run_batch(opts(work_dir, overwrite=True, backup=True))
    baks = list(work_dir.glob("IMG_0001_normal.xmp.bak-*"))
    assert len(baks) == 1 and baks[0].read_text() == "HUMAN EDIT"


def test_dry_run_writes_no_xmp_but_reports(work_dir, tmp_path):
    rep = tmp_path / "rep"
    res = run_batch(opts(work_dir, dry_run=True, report_dir=str(rep)))
    assert not list(work_dir.glob("*.xmp")) and not list(work_dir.glob("*.json"))
    assert {r.status for r in res} == {"analyzed"}
    assert len(list(rep.glob("*.analysis.json"))) == 9


def test_analysis_report_contents(work_dir):
    run_batch(opts(work_dir, style="portrait"))
    data = json.loads((work_dir / "IMG_0006_warm_cast.analysis.json").read_text(encoding="utf-8"))
    for k in ("metadata", "exposure_analysis", "white_balance", "clipping", "colors", "skin", "tone_curve",
              "estimates", "parameters", "validation", "style"):
        assert k in data, k
    assert data["metadata"]["iso"] == 200 and data["style"] == "portrait"
    e = data["estimates"]["exposure"]
    assert {"value", "confidence", "reason"} <= set(e) and e["reason"]
    assert data["white_balance"]["mode"] == "Custom"
    assert data["validation"]["ok"] is True
    assert set(data["colors"]["bands"]) == {"red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"}


def test_corrupt_file_does_not_stop_batch(work_dir, caplog):
    (work_dir / "IMG_9999_broken.CR2").write_bytes(b"garbage")
    res = run_batch(opts(work_dir))
    by = {Path(r.path).name: r for r in res}
    assert by["IMG_9999_broken.CR2"].status == "error"
    assert sum(r.status == "written" for r in res) == 9
    assert not (work_dir / "IMG_9999_broken.xmp").exists()


def test_recursive_and_output_dir_preserve_structure(tmp_path, raw_set):
    root = tmp_path / "ENSAIOS"
    for name, k in (("Ensaio01", "normal"), ("Ensaio02", "underexposed")):
        (root / name).mkdir(parents=True)
        shutil.copy(raw_set[k], root / name / f"IMG_{name}.CR2")
    assert run_batch(opts(root)) == []                                   # not recursive: nothing at the top level
    out = tmp_path / "OUT"
    res = run_batch(opts(root, recursive=True, output_dir=str(out)))
    assert len(res) == 2
    assert (out / "Ensaio01" / "IMG_Ensaio01.xmp").exists() and (out / "Ensaio02" / "IMG_Ensaio02.xmp").exists()
    assert not list(root.rglob("*.xmp"))                                 # sources untouched
    res = run_batch(opts(root, recursive=True))
    assert (root / "Ensaio01" / "IMG_Ensaio01.xmp").exists()


def test_xmp_is_never_written_over_a_raw(tmp_path):
    from rawxmp.pipeline.batch import _atomic_write
    with pytest.raises(ValueError):
        _atomic_write(tmp_path / "x.CR2", "data", True, False)


def test_find_raws_extension_case_insensitive(tmp_path):
    for n in ("a.CR2", "b.cr2", "c.jpg", "d.CR3"):
        (tmp_path / n).write_bytes(b"x")
    assert [p.name for p in find_raws(tmp_path, False, [".cr2"])] == ["a.CR2", "b.cr2"]


def test_parallel_workers_give_same_result_as_serial(work_dir, tmp_path):
    other = tmp_path / "par"
    shutil.copytree(work_dir, other)
    run_batch(opts(work_dir, workers=1))
    run_batch(opts(other, workers=2))
    for p in raws(work_dir):
        assert (work_dir / p.with_suffix(".xmp").name).read_text() == (other / p.with_suffix(".xmp").name).read_text()


def test_consistency_pulls_only_uncertain_estimates():
    from rawxmp.pipeline.consistency import apply_consistency
    cfg = load_config()
    def pk(i, k, conf, mode="As Shot"):
        return {"status": "analyzed", "path": f"/s/IMG{i}.CR2", "camera": "C",
                "params": {"WhiteBalance": mode}, "wb": {"kelvin": k, "tint": 0.0, "confidence": conf,
                                                          "as_shot_kelvin": k, "as_shot_tint": 0.0}}
    sure = pk(1, 5000, 0.95)
    packets = [sure, pk(2, 5200, 0.95), pk(3, 5100, 0.95), pk(4, 5600, 0.2)]
    notes = apply_consistency(packets, cfg)
    assert sure["wb"]["kelvin"] == 5000                                  # confident → untouched
    assert 5100 < packets[3]["wb"]["kelvin"] < 5600 and notes


def test_consistency_ignores_small_groups_and_outliers():
    from rawxmp.pipeline.consistency import apply_consistency
    cfg = load_config()
    mk = lambda i, k, c: {"status": "analyzed", "path": f"/s/{i}.CR2", "camera": "C", "params": {"WhiteBalance": "As Shot"},
                          "wb": {"kelvin": k, "tint": 0.0, "confidence": c, "as_shot_kelvin": k, "as_shot_tint": 0.0}}
    assert apply_consistency([mk(1, 5000, .2), mk(2, 5200, .2)], cfg) == []        # < 3 images
    pk = [mk(1, 5000, .9), mk(2, 5100, .9), mk(3, 5200, .9), mk(4, 9500, .1)]       # tungsten/daylight outlier
    apply_consistency(pk, cfg)
    assert pk[3]["wb"]["kelvin"] == 9500


# ---- CLI ---------------------------------------------------------------------------------------
def test_cli_process_and_exit_codes(work_dir, capsys):
    assert main(["process", str(work_dir), "--workers", "1", "-q"]) == 0
    assert len(list(work_dir.glob("*.xmp"))) == 9
    assert main(["process", str(work_dir), "--workers", "1", "-q"]) == 0          # all skipped
    assert main(["process", str(work_dir / "nope"), "-q"]) == 1                    # missing path
    (work_dir / "x.CR2").write_bytes(b"bad")
    assert main(["process", str(work_dir), "--workers", "1", "-q"]) == 2          # partial failure
    only_bad = work_dir.parent / "only_bad"
    only_bad.mkdir()
    (only_bad / "y.CR2").write_bytes(b"bad")
    assert main(["process", str(only_bad), "--workers", "1", "-q"]) == 1          # total failure


def test_cli_analyze_never_writes_xmp(work_dir, tmp_path, capsys):
    assert main(["analyze", str(work_dir), "--workers", "1", "--report-dir", str(tmp_path / "r"), "-q"]) == 0
    assert not list(work_dir.glob("*.xmp"))
    out = capsys.readouterr().out
    assert "Exp" in out and "conf" in out and "WB" in out and "Pele" in out


def test_cli_dry_run_flag_on_process(work_dir, tmp_path, capsys):
    assert main(["process", str(work_dir), "--dry-run", "--workers", "1", "--report-dir", str(tmp_path / "r"), "-q"]) == 0
    assert not list(work_dir.glob("*.xmp"))


def test_cli_validate(work_dir, capsys):
    main(["process", str(work_dir), "--workers", "1", "-q"])
    assert main(["validate", str(work_dir)]) == 0
    bad = work_dir / "bad.xmp"
    bad.write_text("<nope", encoding="utf-8")
    assert main(["validate", str(work_dir)]) == 1


def test_cli_styles_and_unknown_style(work_dir, capsys):
    assert main(["styles"]) == 0 and "portrait" in capsys.readouterr().out
    assert main(["process", str(work_dir), "--style", "zzz", "-q"]) == 1


def test_log_format(work_dir, capsys):
    configure()
    main(["process", str(work_dir), "--workers", "1"])
    out = capsys.readouterr().out
    for needle in ("[INFO] Processando IMG_0001_normal.CR2", "[INFO] ISO: 200", "[INFO] Exposure calculada:",
                   "[INFO] Skin detected:", "[INFO] XMP criado", "[INFO] Concluído"):
        assert needle in out, needle
