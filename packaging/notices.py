"""Licence texts and notices for a distributable bundle.

A bundle redistributes every runtime dependency, so it must carry their licences: notices for
the permissive ones, and for the LGPL ones (Qt/PySide6, pynput, python-xlib) the full licence
text and where their source is. The list is derived from the installed metadata of
``superhero-control`` and its runtime requirements, so it follows ``uv.lock`` by itself.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
_LICENCE_WORDS = ("LICEN", "COPYING", "NOTICE", "AUTHORS")
# Texts some packages declare but do not ship; supplied from packaging/licenses/ (verbatim
# upstream texts). LGPL-3.0 must travel with GPL-3.0.
# A package that ships no licence file at all gets packaging/licenses/<name>.txt, copied
# from its repository at the locked version.
# Keys are lower-case spellings found in package metadata.
_COMMON_TEXTS = {
    ("lgpl-3.0", "lgplv3", "lgpl 3", "lgpl-3", "lesser general public license v3"): (
        "LGPL-3.0.txt",
        "GPL-3.0.txt",
    ),
    ("gpl-3.0", "gplv3", "general public license v3"): ("GPL-3.0.txt",),
    ("apache-2.0", "apache 2.0", "apache software license", "apache license 2.0"): (
        "Apache-2.0.txt",
    ),
}


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    licence: str
    homepage: str
    files: tuple[Path, ...]  # licence files shipped in the package's own metadata

    @property
    def folder(self) -> str:
        return f"{self.name}-{self.version}"

    @property
    def common_texts(self) -> tuple[str, ...]:
        licence, found = self.licence.lower(), []
        for spellings, texts in _COMMON_TEXTS.items():
            if any(spelling in licence for spelling in spellings):
                found += [t for t in texts if t not in found]
        return tuple(found)


def _licence(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [
        c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")
    ]
    short = (meta.get("License") or "").strip().splitlines()
    if short and len(short[0]) <= 60:
        return short[0]
    return "; ".join(classifiers) or "see the project's homepage"


def _homepage(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    urls = [u.split(",", 1)[-1].strip() for u in meta.get_all("Project-URL") or []]
    return meta.get("Home-page") or next(iter(urls), "") or f"https://pypi.org/project/{dist.name}/"


def runtime_components(root: str = "superhero-control") -> list[Component]:
    """Every installed distribution ``root`` needs at run time, on this platform."""
    seen: dict[str, Component] = {}
    stack = [root]
    while stack:
        name = canonicalize_name(stack.pop())
        if name in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # a requirement whose marker does not apply here
        files = tuple(
            Path(str(dist.locate_file(f)))
            for f in dist.files or ()
            if any(word in f.name.upper() for word in _LICENCE_WORDS)
            and Path(str(dist.locate_file(f))).is_file()
        )
        supplied = HERE / "licenses" / f"{name}.txt"  # for packages that ship no text
        if not files and supplied.is_file():
            files = (supplied,)
        seen[name] = Component(
            name, dist.version, _licence(dist), _homepage(dist), tuple(sorted(set(files)))
        )
        for requirement in dist.requires or ():
            req = Requirement(requirement)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                stack.append(req.name)
    return sorted(seen.values(), key=lambda c: c.name)


def write_notices(target: Path, components: list[Component]) -> list[Component]:
    """Write ``LICENSE``, ``NOTICE``, ``licenses/`` and ``THIRD_PARTY_NOTICES.txt`` into a bundle.

    Returns the components for which no licence text could be included, so the build can
    say so instead of shipping a silent gap.
    """
    target.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(ROOT / name, target / name)
    licences = target / "licenses"
    if licences.exists():
        shutil.rmtree(licences)
    common = licences / "common"
    common.mkdir(parents=True)
    for text in ("LGPL-3.0.txt", "GPL-3.0.txt", "Apache-2.0.txt"):
        shutil.copyfile(HERE / "licenses" / text, common / text)

    missing: list[Component] = []
    lines = [
        "Third-party software in this SUPERHERO bundle",
        "=" * 46,
        "",
        "Each component is an unmodified upstream release. Its licence text is in",
        "licenses/<name>-<version>/ (as shipped by the component) and/or licenses/common/.",
        "",
        "The LGPL-licensed components (Qt for Python / PySide6 and Shiboken, pynput,",
        "python-xlib) are separate, replaceable files in this folder: you may replace them",
        "with your own builds. Their complete source code is available from the homepages",
        "below, at the versions listed.",
        "",
    ]
    for component in components:
        folder = licences / component.folder
        for path in component.files:
            folder.mkdir(exist_ok=True)
            destination = folder / path.name
            if destination.exists():  # two files with one name (e.g. licenses/ and root)
                destination = folder / f"{path.parent.name}-{path.name}"
            shutil.copyfile(path, destination)
        texts = [f"licenses/{component.folder}/"] if component.files else []
        texts += [f"licenses/common/{t}" for t in component.common_texts]
        if not texts:
            missing.append(component)
        lines += [
            f"{component.name} {component.version}",
            f"  Licence:  {component.licence}",
            f"  Source:   {component.homepage}",
            f"  Text:     {', '.join(texts) if texts else 'not shipped by the package; see Source'}",
            "",
        ]
    (target / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(lines), encoding="utf-8")
    return missing
