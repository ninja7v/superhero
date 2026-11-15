import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Never touch the real profile or calibration directories from tests."""
    monkeypatch.setenv("SUPERHERO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SUPERHERO_DATA_DIR", str(tmp_path / "data"))
