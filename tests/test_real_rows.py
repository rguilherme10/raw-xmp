"""Regression on recorded results of the user's own 57 CR2+XMP pairs (Canon T6i; paths stripped)."""
import csv
from pathlib import Path

import numpy as np

from rawxmp.config import load_config
from rawxmp.types import clamp

ROWS = list(csv.DictReader(open(Path(__file__).parent / "data" / "real_rows_t6i.csv", encoding="utf-8")))


def test_measured_noise_never_asks_for_more_luminance_nr_than_the_user_applies():
    d = load_config().section("detail")
    human = np.array([float(r["LuminanceSmoothing_human"]) for r in ROWS])
    assert set(human) == {0.0}                                   # the user applies no luminance NR at all
    extra = [clamp((float(r["noise_rel"]) - d["noise_free_relative"]) * d["noise_gain"], 0, 100) for r in ROWS]
    assert max(extra) == 0.0                                      # measured 3.2–7.8 % relative noise stays below the free level


def test_recorded_exposure_errors_by_group_document_the_calibration_target():
    e = np.array([float(r["Exposure2012_algo"]) - float(r["Exposure2012_human"]) for r in ROWS])
    skin = np.array([int(r["skin_led"]) for r in ROWS]) == 1
    assert abs(e[skin].mean()) < 0.1 and abs(e[skin]).mean() < 0.3          # skin-led photos were already well calibrated
    assert e[~skin].mean() < -0.2                                           # no-skin photos were too dark → target_median 0.14 → 0.17
    assert load_config().get("exposure.target_median") >= 0.17
