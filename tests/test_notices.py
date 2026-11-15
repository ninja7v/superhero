"""The licence notices written into distributable bundles (packaging/notices.py)."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("notices", ROOT / "packaging" / "notices.py")
notices = importlib.util.module_from_spec(_spec)
sys.modules["notices"] = notices  # dataclasses resolve their module by name
_spec.loader.exec_module(notices)


def test_every_runtime_component_gets_a_licence_text(tmp_path):
    components = notices.runtime_components()
    names = {c.name for c in components}
    assert {"superhero-control", "fovea-input", "pyside6-essentials", "pynput", "numpy"} <= names
    assert "pytest" not in names and "ruff" not in names  # development tools are not shipped
    missing = notices.write_notices(tmp_path, components)
    if sys.platform.startswith("linux"):  # verified here; elsewhere the build lists any gap
        assert missing == [], [(c.name, c.licence) for c in missing]
    assert all(not c.files and not c.common_texts for c in missing)
    text = (tmp_path / "THIRD_PARTY_NOTICES.txt").read_text()
    for component in components:
        assert f"{component.name} {component.version}" in text


def test_lgpl_components_carry_the_full_lgpl_and_gpl_texts(tmp_path):
    components = notices.runtime_components()
    notices.write_notices(tmp_path, components)
    common = tmp_path / "licenses" / "common"
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in (common / "LGPL-3.0.txt").read_text()
    assert "GNU GENERAL PUBLIC LICENSE" in (common / "GPL-3.0.txt").read_text()
    assert "Apache License" in (common / "Apache-2.0.txt").read_text()
    qt = next(c for c in components if c.name == "pyside6-essentials")
    assert qt.common_texts == ("LGPL-3.0.txt", "GPL-3.0.txt")
    assert (tmp_path / "LICENSE").read_text() == (ROOT / "LICENSE").read_text()
    assert (tmp_path / "NOTICE").is_file()


def test_licence_spellings_map_to_common_texts():
    def make(licence):
        return notices.Component("x", "1", licence, "", ())

    assert make("LGPLv3").common_texts == ("LGPL-3.0.txt", "GPL-3.0.txt")
    assert make("Apache 2.0").common_texts == ("Apache-2.0.txt",)
    assert make("MIT").common_texts == ()
