import hashlib
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import make_test_raws as gen  # noqa: E402

from rawxmp.config import load_config  # noqa: E402


@pytest.fixture(scope="session")
def raw_set(tmp_path_factory) -> dict:
    """Synthetic RAW set (one file per scenario), generated once per test session."""
    d = tmp_path_factory.mktemp("raws")
    paths = gen.generate_set(d, ".CR2")
    return {p.stem.split("_", 2)[2]: p for p in paths}


@pytest.fixture(scope="session")
def jpeg_set(tmp_path_factory) -> dict:
    d = tmp_path_factory.mktemp("jpegs")
    return {p.stem.split("_", 2)[2]: p for p in gen.generate_jpeg_set(d)}


@pytest.fixture()
def jpeg_dir(tmp_path, jpeg_set):
    d = tmp_path / "jpgs"
    d.mkdir()
    for p in jpeg_set.values():
        shutil.copy(p, d / p.name)
    return d


@pytest.fixture()
def work_dir(tmp_path, raw_set):
    """Fresh copy of the RAW set that tests may write next to."""
    d = tmp_path / "ensaio"
    d.mkdir()
    for p in raw_set.values():
        shutil.copy(p, d / p.name)
    return d


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def cfg_portrait():
    return load_config("portrait")


@pytest.fixture(scope="session")
def decisions(raw_set, cfg_portrait):
    from rawxmp.correction.engine import analyze_raw
    return {k: analyze_raw(p, cfg_portrait) for k, p in raw_set.items()}


def md5(p: Path) -> str:
    return hashlib.md5(Path(p).read_bytes()).hexdigest()
