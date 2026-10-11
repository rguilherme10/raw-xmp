"""Regression against the user's real CR2 + human XMP pairs (skipped if the folder is absent)."""
from pathlib import Path

import pytest

from rawxmp.config import load_config
from rawxmp.correction.engine import analyze_raw
from rawxmp.xmp.parser import parse_xmp

DIR = Path(__file__).resolve().parents[1] / "exemplo-para-calibrar"
PAIRS = sorted(DIR.glob("*.CR2")) if DIR.exists() else []
pytestmark = pytest.mark.skipif(not PAIRS, reason="exemplo-para-calibrar/ not present")


@pytest.fixture(scope="module")
def results():
    """Only per-photo edits: pairs sharing one Exposure value (>= 3 photos) are a pasted preset and are not ground truth."""
    from collections import Counter
    cfg = load_config()
    humans = {p: parse_xmp(p.with_suffix(".xmp")) for p in PAIRS}
    counts = Counter(h.get_float("Exposure2012") for h in humans.values())
    return [(analyze_raw(p, cfg), h) for p, h in humans.items() if counts[h.get_float("Exposure2012")] < 3]


def test_exposure_tracks_human_edits(results):
    errs = sorted(abs(dec.params["Exposure2012"] - human.get_float("Exposure2012")) for dec, human in results)
    assert len(errs) >= 8
    assert sum(errs) / len(errs) <= 0.45                      # mean absolute error in EV over the user's own edits
    assert sum(e <= 0.5 for e in errs) >= 0.75 * len(errs)    # one known outlier (IMG_3004) is tolerated


def test_human_places_skin_at_a_consistent_luminance(results):
    ys = [dec.skin.median_y * 2 ** human.get_float("Exposure2012") for dec, human in results if dec.skin.detected]
    assert len(ys) >= 3 and 0.08 < sorted(ys)[1] and sorted(ys)[-2] < 0.18   # basis of exposure.skin_target_y (outliers tolerated)


def test_real_camera_matrix_is_verified(results):
    for dec, _ in results:
        assert "verified" in dec.info.matrix_note and 4000 < dec.wb.as_shot_temperature < 7000
