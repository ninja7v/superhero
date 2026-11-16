"""Decide which CI work a change needs. Standard library only: it runs before any install.

    python3 .github/scripts/ci_plan.py --event pull_request BASE HEAD

Tests are picked by imports: a changed module selects every test file that imports it,
directly or through other modules or test helpers. A file this script does not recognise
runs everything, so a gap here costs CI time, never coverage. Results go to
``$GITHUB_OUTPUT`` and ``$GITHUB_STEP_SUMMARY`` when set, and always to stdout.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALL_OS = ["ubuntu-latest", "windows-latest", "macos-latest"]
LINUX = ["ubuntu-latest"]

# Files that cannot change what CI checks: prose, the docs folder, repository housekeeping.
NO_EFFECT_SUFFIXES = (".md",)
NO_EFFECT_PREFIXES = ("docs/",)
NO_EFFECT_PATHS = (".gitignore", ".gitattributes")

# Shared infrastructure: dependencies, test harness, CI itself. Any change runs everything.
EVERYTHING = (
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "tests/conftest.py",
    "tests/__init__.py",
    ".github/",
)

# Inputs of the PyInstaller bundle besides the Python code. LICENSE and NOTICE are copied in.
PACKAGE_INPUTS = ("packaging/", "LICENSE", "NOTICE")
# Tests that exercise those inputs without importing them (they load packaging/ by path).
PACKAGE_TESTS = ("tests/test_notices.py",)

# Code whose behaviour cannot depend on the operating system: pure logic over plain data, no
# files, sockets, native libraries or real timing. Where it picks per platform (a shortcut,
# a prompt), the tests force each platform value themselves. A change confined
# to these files is tested on Linux only; anything else also runs on Windows and macOS.
# Keep this list short: a wrong entry hides an OS-specific regression until main.
PORTABLE = frozenset(
    {
        "src/superhero/ai/planner.py",
        "src/superhero/interaction/commands.py",
        "src/superhero/interaction/engine.py",
        "src/superhero/perception/face_gestures.py",
        "src/superhero/voice/audio.py",
        "tests/test_ci_plan.py",
        "tests/test_commands.py",
        "tests/test_engine.py",
        "tests/test_face_gestures.py",
    }
)

_IMPORT_LINE = re.compile(r"^[+-]\s*(import|from)\s+\S", re.MULTILINE)


@dataclass
class Plan:
    lint: bool = False
    tests: bool = False
    test_files: list[str] = field(default_factory=list)  # empty with tests=True: all of them
    os: list[str] = field(default_factory=list)
    package: bool = False
    reasons: list[str] = field(default_factory=list)

    def outputs(self) -> dict[str, str]:
        return {
            "lint": str(self.lint).lower(),
            "tests": str(self.tests).lower(),
            "test_files": " ".join(self.test_files),
            # Never empty: GitHub rejects an empty matrix even on a job it then skips.
            "os": json.dumps(self.os or LINUX),
            "package": str(self.package).lower(),
        }


def no_effect(path: str) -> bool:
    return (
        path.endswith(NO_EFFECT_SUFFIXES)
        or path.startswith(NO_EFFECT_PREFIXES)
        or path in NO_EFFECT_PATHS
    )


def everything(reason: str) -> Plan:
    return Plan(True, True, [], list(ALL_OS), True, [reason])


# -- the import graph ---------------------------------------------------------------------


def module_name(path: str) -> str | None:
    """``src/superhero/a/b.py`` -> ``superhero.a.b``; ``tests/x.py`` -> ``tests.x``."""
    if not path.endswith(".py"):
        return None
    if path.startswith("src/"):
        parts = path[len("src/") : -3].split("/")
    elif path.startswith("tests/"):
        parts = path[:-3].split("/")
    else:
        return None
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _with_parents(name: str) -> set[str]:
    """Importing ``a.b.c`` runs ``a`` and ``a.b`` first, so it depends on them too."""
    parts = name.split(".")
    return {".".join(parts[:i]) for i in range(1, len(parts) + 1)}


def imported_names(source: str, module: str, is_package: bool) -> set[str]:
    """Every module name ``source`` may import, wherever the import statement sits."""
    names: set[str] = set()
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names |= _with_parents(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                anchor = parts[: len(parts) - (node.level - 1)]
                base = ".".join([*anchor, base] if base else anchor)
            names |= _with_parents(base)
            # ``from a import b`` may import the module ``a.b``.
            names |= {f"{base}.{alias.name}" for alias in node.names if alias.name != "*"}
    return names


def build_graph(root: Path) -> dict[str, set[str]]:
    """Module -> the names it imports, for every module under src/ and tests/."""
    graph: dict[str, set[str]] = {}
    for top in ("src", "tests"):
        for file in sorted((root / top).rglob("*.py")):
            name = module_name(file.relative_to(root).as_posix())
            if name:
                source = file.read_text(encoding="utf-8")
                graph[name] = imported_names(source, name, file.name == "__init__.py")
    return graph


def dependents(graph: dict[str, set[str]], changed: set[str]) -> set[str]:
    """Modules that import any of ``changed``, directly or transitively (changed included)."""
    hit = set(changed)
    grew = True
    while grew:
        grew = False
        for module, names in graph.items():
            if module not in hit and names & hit:
                hit.add(module)
                grew = True
    return hit


# -- the decision ----------------------------------------------------------------------


def imports_changed(diff: str) -> bool:
    """Whether a unified diff adds or removes an import (which can change the bundle)."""
    return bool(_IMPORT_LINE.search(diff))


def _package_modules(root: Path, path: str) -> set[str]:
    """A data file belongs to the nearest package; every module of that package may read it."""
    folder = (root / path).parent
    while folder != root and not (folder / "__init__.py").exists():
        folder = folder.parent
    if folder == root:
        return set()
    return {
        name
        for file in folder.rglob("*.py")
        if (name := module_name(file.relative_to(root).as_posix()))
    }


def plan(
    changed: list[str], *, root: Path = ROOT, diff: str = "", event: str = "pull_request"
) -> Plan:
    """What to run for ``changed`` paths (repository-relative, deleted ones included)."""
    if event in ("schedule", "workflow_dispatch"):
        return everything(f"{event}: full run")

    relevant = [p for p in changed if not no_effect(p)]
    if not relevant:
        return Plan(reasons=["documentation only: nothing to run"])
    for path in relevant:
        if path.startswith(EVERYTHING):
            return everything(f"{path}: shared infrastructure, everything runs")

    graph = build_graph(root)
    all_tests = sorted(
        f"{name.replace('.', '/')}.py"
        for name in graph
        if name.startswith("tests.") and name.rpartition(".")[2].startswith("test_")
    )
    modules: set[str] = set()
    extra_tests: set[str] = set()
    package = False
    reasons: list[str] = []

    for path in relevant:
        name = module_name(path)
        if name and path.startswith(("src/superhero/", "tests/")):
            modules.add(name)
        elif path.startswith("src/superhero/"):  # a data file such as a UI icon
            modules |= _package_modules(root, path)
            package = True
            reasons.append(f"{path}: bundled resource")
        elif path.startswith("tests/fixtures/"):  # read by path, invisible to imports
            extra_tests |= set(all_tests)
        elif path.startswith(PACKAGE_INPUTS):
            extra_tests |= set(PACKAGE_TESTS)
            package = True
            reasons.append(f"{path}: bundle input")
        else:
            return everything(f"{path}: not recognised, so everything runs")

    hit = dependents(graph, modules)
    selected = sorted({t for t in all_tests if module_name(t) in hit} | extra_tests)
    if not package and imports_changed(diff):
        package = True
        reasons.append("an import changed: the bundle may need different modules")

    portable = all(p in PORTABLE for p in relevant)
    reasons.append(
        "only OS-independent logic changed: Linux only"
        if portable
        else "OS-dependent code changed: Linux, Windows and macOS"
    )
    return Plan(
        lint=any(p.endswith(".py") for p in relevant),
        tests=True,
        test_files=[] if selected == all_tests else selected,
        os=list(LINUX if portable else ALL_OS),
        package=package,
        reasons=reasons,
    )


# -- command line ------------------------------------------------------------------------


def _git(*args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True, errors="replace"
    )  # a non-UTF-8 file must not stop the decision
    return done.stdout


def _known_commit(sha: str) -> bool:
    if not sha or set(sha) == {"0"}:
        return False
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=ROOT, capture_output=True
    )
    return probe.returncode == 0


def summary(result: Plan, changed: list[str]) -> str:
    tests = " ".join(result.test_files) or ("all" if result.tests else "none")
    lines = [
        "### CI plan",
        "",
        "| check | runs |",
        "|---|---|",
        f"| lint | {'yes' if result.lint else 'no'} |",
        f"| tests | {tests} |",
        f"| operating systems | {', '.join(result.os) or 'none'} |",
        f"| package (PyInstaller) | {'yes' if result.package else 'no'} |",
        "",
        *[f"- {r}" for r in result.reasons],
        "",
        f"<details><summary>{len(changed)} changed files</summary>",
        "",
        *[f"- `{p}`" for p in changed],
        "",
        "</details>",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decide which CI work a change needs.")
    parser.add_argument("--event", default="pull_request")
    parser.add_argument("base", nargs="?", default="")
    parser.add_argument("head", nargs="?", default="HEAD")
    args = parser.parse_args(argv)

    changed: list[str] = []
    if args.event in ("schedule", "workflow_dispatch"):
        result = plan([], event=args.event)
    elif not (_known_commit(args.base) and _known_commit(args.head)):
        result = everything(f"cannot diff {args.base or '(none)'}..{args.head}: everything runs")
    else:
        # Compared from where the branch left the base (three dots), so every commit on it
        # counts. --no-renames lists a moved file under both its old and new name.
        span = f"{args.base}...{args.head}"
        # -z: paths verbatim, even with spaces or non-ASCII characters.
        listing = _git("diff", "--name-only", "-z", "--no-renames", span)
        changed = [path for path in listing.split("\0") if path]
        diff = _git("diff", "--unified=0", "--no-renames", span, "--", "src")
        result = plan(changed, diff=diff, event=args.event)

    text = summary(result, changed)
    outputs = "".join(f"{k}={v}\n" for k, v in result.outputs().items())
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            out.write(outputs)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write(text)
    sys.stdout.write(outputs + "\n" + text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
