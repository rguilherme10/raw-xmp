"""Camera JPEG support: analysis + XMP embedded in a COPY (the original is never touched)."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import md5
from rawxmp.cli.main import main
from rawxmp.config import load_config
from rawxmp.correction.engine import analyze_raw
from rawxmp.pipeline.batch import Options, run_batch
from rawxmp.pipeline.discovery import find_raws
from rawxmp.raw.jpeg import JpegError, embed_xmp, extract_exif_tiff, extract_xmp, read_jpeg_info
from rawxmp.validation.xmp_validator import validate_file
from rawxmp.xmp.parser import parse_xmp


def opts(d, **kw):
    kw.setdefault("workers", 1)
    kw.setdefault("jpeg", True)
    return Options(path=str(d), **kw)


def outs(d: Path):
    return sorted((d / "rawxmp_jpeg").glob("*.JPG"))


# ---- container level ---------------------------------------------------------------------------
def test_embed_keeps_pixels_exif_and_original(jpeg_set, tmp_path):
    src = jpeg_set["normal"]
    before = md5(src)
    dst = tmp_path / "o" / "x.jpg"
    embed_xmp(src, dst, '<x:xmpmeta xmlns:x="adobe:ns:meta/"><a>é</a></x:xmpmeta>')
    assert md5(src) == before and extract_xmp(src) is None
    assert "é" in extract_xmp(dst)
    assert (np.asarray(Image.open(src)) == np.asarray(Image.open(dst))).all()          # not re-encoded
    assert extract_exif_tiff(dst) is not None
    embed_xmp(dst, dst.with_name("y.jpg"), "<x:xmpmeta/>")                              # replaces, never duplicates
    assert extract_xmp(dst.with_name("y.jpg")) == "<x:xmpmeta/>"


def test_embed_rejects_non_jpeg_and_huge_packets(tmp_path, jpeg_set):
    bad = tmp_path / "b.jpg"
    bad.write_bytes(b"not a jpeg")
    with pytest.raises(JpegError):
        embed_xmp(bad, tmp_path / "o.jpg", "<x/>")
    with pytest.raises(JpegError):
        embed_xmp(jpeg_set["normal"], tmp_path / "o.jpg", "x" * 70000)


def test_exif_is_read(jpeg_set):
    i = read_jpeg_info(jpeg_set["high_iso"])
    assert i.camera == "Canon EOS SYNTH" and i.iso == 6400 and i.aperture == pytest.approx(2.8) and i.width == 480


# ---- analysis ------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def jdec(jpeg_set):
    cfg = load_config()
    return {k: analyze_raw(p, cfg) for k, p in jpeg_set.items()}


def test_jpeg_analysis_is_conservative_and_directional(jdec):
    assert jdec["underexposed"].params["Exposure2012"] > jdec["normal"].params["Exposure2012"] + 0.3
    assert jdec["overexposed"].params["Exposure2012"] <= 0 and jdec["overexposed"].params["Highlights2012"] < -10
    for d in jdec.values():
        assert d.kind == "jpeg" and abs(d.params["Exposure2012"]) <= 1.0 + 1e-9        # config/jpeg.yaml ceiling


def test_jpeg_does_not_write_raw_only_fields(jdec):
    for d in jdec.values():
        assert d.params["WhiteBalance"] == "As Shot" and "Temperature" not in d.params and "Tint" not in d.params
        assert "CameraProfile" not in d.params and "LensProfileEnable" not in d.params


def test_jpeg_skin_detection_works(jdec):
    assert jdec["normal"].skin.detected and not jdec["no_skin"].skin.detected


def test_cool_cast_jpeg_still_finds_skin(jdec):
    assert jdec["cool_cast"].skin.detected                                              # neutralisation works without a camera matrix


# ---- batch ----------------------------------------------------------------------------------------
def test_process_jpeg_writes_embedded_copies_and_never_touches_originals(jpeg_dir):
    before = {p.name: md5(p) for p in jpeg_dir.glob("*.JPG")}
    res = run_batch(opts(jpeg_dir))
    assert {r.status for r in res} == {"written"} and len(res) == 9
    assert before == {p.name: md5(p) for p in jpeg_dir.glob("*.JPG")}
    assert not list(jpeg_dir.glob("*.xmp"))                                             # no sidecar for JPEGs
    cfg = load_config()
    for o in outs(jpeg_dir):
        assert validate_file(o, cfg).ok, o.name
        m = parse_xmp(extract_xmp(o))
        assert m.attrs["HasSettings"] == "True" and "ToneCurvePV2012" in m.curves
        assert (np.asarray(Image.open(o)) == np.asarray(Image.open(jpeg_dir / o.name))).all()
    rep = json.loads((jpeg_dir / "rawxmp_jpeg" / "IMG_0001_normal.analysis.json").read_text(encoding="utf-8"))
    assert rep["source"] == "jpeg" and "estimates" in rep


def test_jpeg_is_opt_in(jpeg_dir):
    assert run_batch(Options(path=str(jpeg_dir), workers=1)) == []                      # no --jpeg: RAW only, nothing found


def test_existing_copy_skipped_overwrite_and_backup(jpeg_dir):
    run_batch(opts(jpeg_dir))
    again = run_batch(opts(jpeg_dir))
    assert {r.status for r in again} == {"skipped"}
    res = run_batch(opts(jpeg_dir, overwrite=True, backup=True))
    assert {r.status for r in res} == {"written"} and len(list((jpeg_dir / "rawxmp_jpeg").glob("*.bak-*"))) == 9


def test_output_dir_and_recursive_structure(tmp_path, jpeg_set):
    import shutil
    root = tmp_path / "S"
    (root / "a").mkdir(parents=True)
    (root / "b" / "c").mkdir(parents=True)
    shutil.copy(jpeg_set["normal"], root / "a" / "one.jpg")
    shutil.copy(jpeg_set["underexposed"], root / "b" / "c" / "two.JPG")
    out = tmp_path / "OUT"
    res = run_batch(opts(root, recursive=True, output_dir=str(out)))
    assert len(res) == 2 and (out / "a" / "one.jpg").exists() and (out / "b" / "c" / "two.JPG").exists()
    assert not (root / "rawxmp_jpeg").exists()


def test_default_output_folder_is_not_reprocessed(jpeg_dir):
    run_batch(opts(jpeg_dir))
    names = [p.name for p in find_raws(jpeg_dir, True, [".jpg"])]
    assert len(names) == 9                                                              # the 9 copies are excluded
    assert len(run_batch(opts(jpeg_dir, recursive=True))) == 9


def test_raw_plus_jpeg_pairs_are_not_duplicated(tmp_path, raw_set, jpeg_set):
    import shutil
    d = tmp_path / "pair"
    d.mkdir()
    shutil.copy(raw_set["normal"], d / "IMG_A.CR2")
    shutil.copy(jpeg_set["normal"], d / "IMG_A.JPG")
    shutil.copy(jpeg_set["no_skin"], d / "IMG_B.JPG")
    res = run_batch(opts(d))
    names = sorted(Path(r.path).name for r in res)
    assert names == ["IMG_A.CR2", "IMG_B.JPG"]
    res = run_batch(opts(d, jpeg_with_raw=True, overwrite=True))
    assert sorted(Path(r.path).name for r in res) == ["IMG_A.CR2", "IMG_A.JPG", "IMG_B.JPG"]


def test_destination_equal_to_source_is_refused(jpeg_dir):
    before = {p.name: md5(p) for p in jpeg_dir.glob("*.JPG")}
    res = run_batch(opts(jpeg_dir, output_dir=str(jpeg_dir), overwrite=True))
    assert all(r.status == "error" for r in res)
    assert before == {p.name: md5(p) for p in jpeg_dir.glob("*.JPG")}


def test_existing_embedded_xmp_is_preserved(jpeg_dir, jpeg_set):
    from rawxmp.xmp.model import XMPModel
    from rawxmp.xmp.serializer import XMPSerializer
    m = XMPModel()
    m.other_attrs["xmp:Rating"] = "3"
    m.attrs["CustomVendorField"] = "keep-me"
    src = jpeg_dir / "IMG_0001_normal.JPG"
    tmp = jpeg_dir / "tmp.jpg"
    embed_xmp(src, tmp, XMPSerializer.serialize(m))
    tmp.replace(src)
    run_batch(opts(jpeg_dir))
    out = parse_xmp(extract_xmp(jpeg_dir / "rawxmp_jpeg" / "IMG_0001_normal.JPG"))
    assert out.other_attrs["xmp:Rating"] == "3" and out.attrs["CustomVendorField"] == "keep-me" and "Exposure2012" in out.attrs


def test_corrupt_jpeg_does_not_stop_the_batch(jpeg_dir):
    (jpeg_dir / "BAD.JPG").write_bytes(b"garbage")
    res = run_batch(opts(jpeg_dir))
    by = {Path(r.path).name: r.status for r in res}
    assert by["BAD.JPG"] == "error" and sum(s == "written" for s in by.values()) == 9


# ---- CLI + calibrate -------------------------------------------------------------------------------
def test_cli_jpeg_flag_validate_and_calibrate(jpeg_dir, tmp_path, capsys):
    assert main(["process", str(jpeg_dir), "--jpeg", "--workers", "1", "-q"]) == 0
    out = jpeg_dir / "rawxmp_jpeg"
    assert main(["validate", str(out)]) == 0
    from rawxmp.calibration.calibrate import calibrate
    s = calibrate(out, load_config(), include_jpeg=True, out_dir=tmp_path / "cal")      # embedded settings act as the 'human' edit
    assert s["n_pairs"] == 9 and s["parameters"]["Exposure2012"]["n"] >= 1


def test_calibrate_ignores_jpegs_without_embedded_settings(jpeg_dir, tmp_path):
    from rawxmp.calibration.calibrate import calibrate
    assert calibrate(jpeg_dir, load_config(), include_jpeg=True, out_dir=tmp_path / "cal")["n_pairs"] == 0
