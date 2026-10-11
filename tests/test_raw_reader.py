import pytest

from conftest import md5
from rawxmp.raw.exif import parse_tiff_exif
from rawxmp.raw.reader import RawReader, RawReadError
from rawxmp.raw.render import render_for_analysis


def test_reads_metadata(raw_set):
    info = RawReader(raw_set["normal"]).read_info()
    assert info.width == 480 and info.height == 320
    assert info.iso == 200 and info.aperture == pytest.approx(2.8)
    assert info.shutter == pytest.approx(1 / 200, rel=1e-3)
    assert info.focal_length == pytest.approx(50.0)
    assert info.camera == "Canon EOS SYNTH" and "50mm" in info.lens
    assert info.datetime == "2024:05:01 10:20:30" and info.orientation == 1
    assert info.black_level == [256] * 4 and info.white_level == 16383
    assert len(info.camera_wb) == 3 and info.camera_wb[1] == pytest.approx(1.0)
    assert info.xyz_to_cam is not None and len(info.xyz_to_cam) == 3
    assert info.camera_neutral is not None


def test_high_iso_metadata(raw_set):
    assert RawReader(raw_set["high_iso"]).read_info().iso == 6400


def test_tiff_parser_standalone(raw_set):
    m = parse_tiff_exif(raw_set["normal"])
    assert m["make"] == "Canon" and m["iso"] == 200


def test_render_is_linear_float_and_reduced(raw_set):
    img = render_for_analysis(RawReader(raw_set["normal"]), max_dim=200)
    assert img.dtype.name == "float32" and img.ndim == 3 and img.shape[2] == 3
    assert max(img.shape[:2]) == 200
    assert 0.0 <= img.min() and img.max() <= 1.0


def test_render_cache_roundtrip(raw_set, tmp_path):
    r = RawReader(raw_set["normal"])
    a = render_for_analysis(r, 128, cache_dir=tmp_path)
    assert list(tmp_path.glob("*.npy"))
    b = render_for_analysis(r, 128, cache_dir=tmp_path)
    assert (a == b).all()


def test_bad_file_raises_clean_error(tmp_path):
    p = tmp_path / "broken.CR2"
    p.write_bytes(b"this is not a raw file")
    with pytest.raises(RawReadError):
        RawReader(p).read_info()


def test_reading_never_modifies_raw(raw_set):
    before = md5(raw_set["normal"])
    r = RawReader(raw_set["normal"])
    r.read_info()
    render_for_analysis(r, 100)
    assert md5(raw_set["normal"]) == before
