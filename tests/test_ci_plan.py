"""Which CI work each kind of change triggers (.github/scripts/ci_plan.py)."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ci_plan", ROOT / ".github/scripts/ci_plan.py")
ci_plan = importlib.util.module_from_spec(_spec)
sys.modules["ci_plan"] = ci_plan  # dataclasses resolve their module by name
_spec.loader.exec_module(ci_plan)

ALL_OS, LINUX = ci_plan.ALL_OS, ci_plan.LINUX
ALL_TESTS = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py"))


def plan(*changed, **kwargs):
    return ci_plan.plan(list(changed), **kwargs)


def selected_tests(result):
    assert result.tests
    return set(result.test_files or ALL_TESTS)


def test_documentation_only_runs_nothing():
    result = plan(
        "README.md", "docs/ROADMAP.md", "docs/diagram.png", "tests/fixtures/README.md", ".gitignore"
    )
    assert not (result.lint or result.tests or result.package) and result.os == []


@pytest.mark.parametrize(
    ("changed", "must_run", "os"),
    [
        # configuration, profiles, migration and calibration storage
        ("src/superhero/config.py", {"test_config", "test_pipeline", "test_app"}, ALL_OS),
        # camera and frame processing
        ("src/superhero/perception/camera.py", {"test_camera", "test_pipeline"}, ALL_OS),
        ("src/superhero/perception/pipeline.py", {"test_pipeline", "test_app"}, ALL_OS),
        # face gestures: pure geometry, so Linux answers for every OS
        ("src/superhero/perception/face_gestures.py", {"test_face_gestures"}, LINUX),
        # hand tracking reaches the pen demo, which imports it lazily
        ("src/superhero/perception/hands.py", {"test_hands", "test_pen", "test_pipeline"}, ALL_OS),
        # interaction engine and its safety rules, and everything built on it
        ("src/superhero/interaction/engine.py", {"test_engine", "test_ai", "test_app"}, LINUX),
        ("src/superhero/interaction/commands.py", {"test_commands", "test_engine"}, LINUX),
        # Wayland portals
        ("src/superhero/platform/portal.py", {"test_portal", "test_platform"}, ALL_OS),
        # Windows, macOS and X11 injection
        ("src/superhero/platform/pynput_backend.py", {"test_input_backends"}, ALL_OS),
        ("src/superhero/platform/hotkey.py", {"test_input_backends", "test_app"}, ALL_OS),
        # voice
        ("src/superhero/voice/audio.py", {"test_audio", "test_app"}, LINUX),
        ("src/superhero/voice/stt.py", {"test_audio", "test_app"}, ALL_OS),
        ("src/superhero/voice/session.py", {"test_audio", "test_app"}, ALL_OS),
        # AI planner
        ("src/superhero/ai/planner.py", {"test_ai", "test_engine"}, LINUX),
        # overlay and panel
        ("src/superhero/ui/overlay.py", {"test_app"}, ALL_OS),
        ("src/superhero/ui/panel.py", {"test_app"}, ALL_OS),
        # command line: doctor, bench, install-desktop
        ("src/superhero/app.py", {"test_app", "test_platform"}, ALL_OS),
        ("src/superhero/doctor.py", {"test_app"}, ALL_OS),
        # a shared type almost everything uses
        (
            "src/superhero/platform/displays.py",
            {"test_displays", "test_engine", "test_app"},
            ALL_OS,
        ),
    ],
)
def test_a_module_runs_the_tests_that_import_it(changed, must_run, os):
    result = plan(changed)
    selected = selected_tests(result)
    assert {f"tests/{name}.py" for name in must_run} <= selected
    assert result.os == os and result.lint and not result.package


def test_unrelated_tests_are_left_out():
    selected = selected_tests(plan("src/superhero/perception/face_gestures.py"))
    assert not selected & {"tests/test_ai.py", "tests/test_portal.py", "tests/test_notices.py"}
    selected = selected_tests(plan("src/superhero/ai/planner.py"))
    assert not selected & {"tests/test_camera.py", "tests/test_hands.py", "tests/test_portal.py"}


def test_a_changed_test_runs_itself_and_the_tests_built_on_it():
    selected = selected_tests(plan("tests/test_engine.py"))
    assert selected == {"tests/test_engine.py", "tests/test_ai.py"}  # test_ai reuses its Rig
    assert "tests/test_app.py" in selected_tests(plan("tests/test_pipeline.py"))  # FakeCamera
    for test in ALL_TESTS:
        assert test in selected_tests(plan(test))


def test_the_package_init_reaches_every_test_that_imports_superhero():
    selected = selected_tests(plan("src/superhero/__init__.py"))
    assert selected >= set(ALL_TESTS) - {"tests/test_notices.py", "tests/test_ci_plan.py"}


@pytest.mark.parametrize(
    "changed",
    [
        "pyproject.toml",
        "uv.lock",
        "tests/conftest.py",
        ".github/workflows/tests.yml",
        ".github/scripts/ci_plan.py",
        "Makefile",  # not recognised
        "src/other_package/thing.py",
    ],
)
def test_shared_infrastructure_and_unknown_files_run_everything(changed):
    result = plan(changed)
    assert result.lint and result.tests and result.test_files == [] and result.package
    assert result.os == ALL_OS


@pytest.mark.parametrize("changed", ["packaging/notices.py", "packaging/build.py", "NOTICE"])
def test_packaging_runs_its_tests_and_builds_every_bundle(changed):
    result = plan(changed)
    assert result.package and result.os == ALL_OS
    assert "tests/test_notices.py" in selected_tests(result)


def test_a_bundled_resource_builds_the_bundle_and_tests_its_package():
    result = plan("src/superhero/ui/assets/chevron-down.svg")
    assert result.package and "tests/test_app.py" in selected_tests(result) and not result.lint


def test_a_fixture_runs_every_test():
    assert selected_tests(plan("tests/fixtures/frontal_30f.jsonl")) == set(ALL_TESTS)


def test_an_added_or_removed_import_builds_the_bundle():
    diff = "@@ -10,0 +11 @@\n+import sounddevice\n"
    assert plan("src/superhero/voice/audio.py", diff=diff).package
    diff = "@@ -40 +40 @@\n-        x = 1\n+        x = 2  # from the import below\n"
    assert not plan("src/superhero/voice/audio.py", diff=diff).package


def test_a_deleted_module_still_selects_the_tests_that_import_it():
    # The file is gone from the tree, but tests naming it must run (and fail) on the PR.
    assert "tests/test_hands.py" in selected_tests(plan("src/superhero/perception/hands.py"))
    ghost = plan("src/superhero/perception/removed_helper.py")
    assert ghost.tests and ghost.os == ALL_OS


def test_mixed_changes_take_the_union_and_the_widest_platform_set():
    result = plan(
        "README.md",
        "src/superhero/ai/planner.py",
        "src/superhero/platform/portal.py",
        "src/superhero/perception/face_gestures.py",
    )
    assert selected_tests(result) >= {
        "tests/test_ai.py",
        "tests/test_portal.py",
        "tests/test_face_gestures.py",
    }
    assert result.os == ALL_OS and not result.package


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_scheduled_and_manual_runs_do_everything(event):
    result = plan(event=event)
    assert result.tests and result.test_files == [] and result.package and result.os == ALL_OS


def test_relative_imports_are_followed():
    names = ci_plan.imported_names("from ..config import X\nfrom . import y", "a.b.c", False)
    assert {"a.config", "a.config.X", "a.b", "a.b.y"} <= names
    names = ci_plan.imported_names("from .engine import E", "a.interaction", True)
    assert "a.interaction.engine" in names


def test_portable_entries_exist():
    """A stale entry would silently narrow nothing, a renamed one could hide a gap."""
    for path in ci_plan.PORTABLE:
        assert (ROOT / path).is_file(), path


def test_outputs_are_what_the_workflow_reads():
    outputs = plan("src/superhero/ai/planner.py").outputs()
    assert set(outputs) == {"lint", "tests", "test_files", "os", "package"}
    assert outputs["os"] == '["ubuntu-latest"]' and outputs["package"] == "false"
    assert all(f.startswith("tests/test_") for f in outputs["test_files"].split())
