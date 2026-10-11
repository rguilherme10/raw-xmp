import json
import shutil
from pathlib import Path

from rawxmp.calibration.calibrate import calibrate
from rawxmp.config import load_config
from rawxmp.pipeline.batch import Options, run_batch
from rawxmp.preview.compare import make_comparison
from rawxmp.preview.simulate import default_render, simulate
import numpy as np


def test_calibration_against_own_output_has_zero_bias(work_dir, tmp_path):
    cfg = load_config()
    run_batch(Options(path=str(work_dir), workers=1, write_reports=False))      # "human" edits = our own output
    s = calibrate(work_dir, cfg, out_dir=tmp_path / "cal")
    assert s["n_pairs"] == 9
    ex = s["parameters"]["Exposure2012"]
    assert ex["bias"] == 0 and ex["mae"] == 0 and ex["n"] == 9
    assert (tmp_path / "cal" / "calibration_summary.json").exists()
    assert (tmp_path / "cal" / "calibration_features.csv").exists()
    assert s["suggestions"]["exposure.target_median"] == cfg.get("exposure.target_median")


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
    assert s["suggestions"]["exposure.target_median"] > cfg.get("exposure.target_median")   # human = brighter → raise target


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
