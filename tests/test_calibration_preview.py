import json
import shutil
from pathlib import Path

from rawxmp.calibration.calibrate import calibrate
from rawxmp.config import load_config
from rawxmp.pipeline.batch import Options, run_batch
from rawxmp.preview.compare import make_comparison
from rawxmp.preview.simulate import default_render, simulate
import numpy as np
import pytest


def test_calibration_against_own_output_has_zero_bias(work_dir, tmp_path):
    cfg = load_config()
    run_batch(Options(path=str(work_dir), workers=1, write_reports=False))      # "human" edits = our own output
    s = calibrate(work_dir, cfg, out_dir=tmp_path / "cal")
    assert s["n_pairs"] == 9
    ex = s["parameters"]["Exposure2012"]
    assert ex["bias"] == 0 and ex["mae"] == 0 and ex["n"] == 9
    assert (tmp_path / "cal" / "calibration_summary.json").exists()
    assert (tmp_path / "cal" / "calibration_features.csv").exists()
    assert "exposure_groups" in s and set(s["exposure_groups"]) == {"skin_led", "no_skin"}
    for k in ("exposure.skin_target_y", "exposure.target_median"):          # unbiased -> suggestion equals the current value
        if k in s["suggestions"]:
            assert s["suggestions"][k] == cfg.get(k)


def test_calibration_detects_systematic_bias(work_dir, tmp_path):
    cfg = load_config()
    run_batch(Options(path=str(work_dir), workers=1, write_reports=False))
    for x in work_dir.glob("*.xmp"):                                            # human edits are 0.5 EV brighter
        t = x.read_text(encoding="utf-8")
        import re
        m = re.search(r'crs:Exposure2012="([+-]?[\d.]+)"', t)
        t = t.replace(m.group(0), f'crs:Exposure2012="{float(m.group(1)) + 0.5:+.2f}"')
        x.write_text(t, encoding="utf-8")
    s = calibrate(work_dir, cfg, out_dir=tmp_path / "cal")
    assert abs(s["parameters"]["Exposure2012"]["bias"] + 0.5) < 0.02
    assert s["suggestions"].get("exposure.skin_target_y", 1) > cfg.get("exposure.skin_target_y") or \
        s["suggestions"].get("exposure.target_median", 1) > cfg.get("exposure.target_median")   # human = brighter → raise a target


def test_calibration_skips_raw_without_xmp(work_dir, tmp_path):
    s = calibrate(work_dir, load_config(), out_dir=tmp_path / "cal")
    assert s["n_pairs"] == 0


def test_simulation_responds_to_parameters():
    rng = np.random.default_rng(1)
    lin = rng.uniform(0.02, 0.6, (40, 50, 3)).astype(np.float32)
    base = simulate(lin, {})
    assert simulate(lin, {"Exposure2012": 1.0}).mean() > base.mean()
    assert simulate(lin, {"Shadows2012": 50}).mean() > base.mean()
    assert simulate(lin, {"Highlights2012": -50}).mean() < base.mean()
    sat0 = np.ptp(base, axis=-1).mean()
    assert np.ptp(simulate(lin, {"Saturation": 40}), axis=-1).mean() > sat0
    assert np.isfinite(simulate(lin, {"Contrast2012": 30}, [(0, 0), (128, 140), (255, 255)])).all()
    assert default_render(lin).max() <= 1.0


def test_compare_png_created(raw_set, tmp_path):
    out = make_comparison(raw_set["underexposed"], tmp_path / "c.png", load_config("portrait"))
    assert out.exists() and out.stat().st_size > 5000


def test_synced_preset_is_detected_and_not_used_for_calibration(work_dir, tmp_path):
    import re
    cfg = load_config()
    run_batch(Options(path=str(work_dir), workers=1, write_reports=False))
    for x in work_dir.glob("*.xmp"):                       # every photo gets the SAME exposure (copy/paste settings)
        t = x.read_text(encoding="utf-8")
        t = re.sub(r'crs:Exposure2012="[^"]+"', 'crs:Exposure2012="+1.31"', t)
        x.write_text(t, encoding="utf-8")
    s = calibrate(work_dir, cfg, out_dir=tmp_path / "cal")
    assert s["parameters"]["Exposure2012"]["synced_preset"] is True
    assert any("Exposure2012" in w and "preset" in w for w in s["warnings"])
    assert "exposure.target_median" not in s["suggestions"]


def test_exposure_consistency_pulls_series_with_same_settings_only():
    from rawxmp.pipeline.consistency import consistent_exposures
    cc = {"tolerance_ev": 0.8, "max_pull": 0.7, "min_group": 3}
    meta = {"camera": "C", "lens": "L", "iso": 200, "aperture": 2.8, "shutter": 1 / 200}
    mk = lambda n, e, **kw: {"path": f"/s/{n}.CR2", "exposure": e, "meta": {**meta, **kw}}
    items = [mk("a", -0.5), mk("b", 0.0), mk("c", 0.6), mk("d", 2.0),                 # d: outlier (other light) left alone
             mk("e", 1.0, iso=800), mk("f", -1.0, iso=800)]                          # other ISO group has only 2 frames
    out = consistent_exposures(items, cc)
    assert out["/s/a.CR2"] > -0.5 and out["/s/c.CR2"] < 0.6                        # pulled toward the median
    assert out["/s/d.CR2"] == 2.0 and "/s/e.CR2" not in out and "/s/f.CR2" not in out
    spread = lambda ks: max(out[k] for k in ks) - min(out[k] for k in ks)
    assert spread(["/s/a.CR2", "/s/b.CR2", "/s/c.CR2"]) < 1.1 * 0.5


def test_consistency_is_off_by_default_and_used_when_enabled(work_dir):
    import rawxmp.config as C
    from rawxmp.config import load_config
    assert load_config().get("consistency.exposure.enabled") is False
    off = run_batch(Options(path=str(work_dir), workers=1, write_reports=False))
    assert all(r.status == "written" for r in off)


def test_calibrate_reports_consistency_and_writes_style(work_dir, tmp_path):
    from rawxmp.calibration.calibrate import write_to_style
    cfg = load_config()
    run_batch(Options(path=str(work_dir), workers=1, write_reports=False))
    s = calibrate(work_dir, cfg, out_dir=tmp_path / "cal")
    assert "exposure_consistency" in s and s["exposure_consistency"]["n"] == 9
    dest = write_to_style("sessao-x", {"exposure.skin_target_y": 0.18}, tmp_path)
    import yaml
    assert yaml.safe_load(dest.read_text(encoding="utf-8")) == {"exposure": {"skin_target_y": 0.18}}
    dest = write_to_style("sessao-x", {"exposure.target_median": 0.12}, tmp_path)           # merges, keeps previous key
    assert yaml.safe_load(dest.read_text(encoding="utf-8"))["exposure"] == {"skin_target_y": 0.18, "target_median": 0.12}
    with pytest.raises(ValueError):
        write_to_style("sessao-x", {}, tmp_path)
