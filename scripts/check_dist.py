"""Validate the exact wheel/sdist pair that will be installed or published."""

from __future__ import annotations

import argparse
import configparser
import stat
import tarfile
import tomllib
import zipfile
from email import message_from_bytes
from pathlib import Path, PurePosixPath

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = {
    "PKG-INFO",
    "pyproject.toml",
    "MANIFEST.in",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "NOTICE",
    "uv.lock",
    "requirements.txt",
    "requirements-dev.txt",
    "server.py",
    "setup.cfg",
}
EGG_INFO_FILES = {
    "PKG-INFO",
    "SOURCES.txt",
    "dependency_links.txt",
    "entry_points.txt",
    "requires.txt",
    "top_level.txt",
}
WHEEL_INFO_FILES = {"METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt"}


def _source_file(name: str) -> bool:
    path = PurePosixPath(name)
    if name in SOURCE_FILES:
        return True
    if path.parts[:2] == ("src", "wos_mcp_bridge"):
        return path.suffix == ".py"
    if path.parts[:2] == ("src", "wos_mcp_bridge.egg-info"):
        return len(path.parts) == 3 and path.name in EGG_INFO_FILES
    if path.parts[0] in {"scripts", "tests"}:
        return path.suffix == ".py"
    return path.parts[0] == "docs" and "superpowers" not in path.parts and path.suffix == ".md"


def _safe_path(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or any(
        part.startswith(".") or part == "__pycache__" for part in path.parts
    ):
        raise ValueError(f"Unexpected archive path: {name}")


def _metadata(raw: bytes, project: dict) -> None:
    metadata = message_from_bytes(raw)
    expected = {
        "Name": project["name"],
        "Version": project["version"],
        "Requires-Python": project["requires-python"],
        "License-Expression": project["license"],
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError("Distribution metadata differs from pyproject.toml.")
    actual = {Requirement(value) for value in metadata.get_all("Requires-Dist", [])}
    if actual != {Requirement(value) for value in project["dependencies"]}:
        raise ValueError("Distribution dependency metadata differs from pyproject.toml.")


def check_distributions(directory: Path, project_root: Path, tag: str | None = None) -> None:
    project = tomllib.loads((project_root / "pyproject.toml").read_text())["project"]
    version = project["version"]
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"Release tag must be v{version}.")
    stem = f"{canonicalize_name(project['name']).replace('-', '_')}-{version}"
    expected = {f"{stem}-py3-none-any.whl", f"{stem}.tar.gz"}
    if {path.name for path in directory.iterdir() if path.name != ".gitignore"} != expected:
        raise ValueError("Expected exactly one matching wheel and source distribution.")
    modules = {
        path.relative_to(project_root / "src").as_posix(): path.read_bytes()
        for path in (project_root / "src" / "wos_mcp_bridge").rglob("*.py")
        if "__pycache__" not in path.parts
    }
    info = f"{stem}.dist-info"
    wheel_files = {}
    with zipfile.ZipFile(directory / f"{stem}-py3-none-any.whl") as archive:
        for entry in archive.infolist():
            _safe_path(entry.filename)
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError(f"Unexpected archive link: {entry.filename}")
            if entry.is_dir():
                continue
            allowed = (
                entry.filename in modules
                or entry.filename in {f"{info}/{name}" for name in WHEEL_INFO_FILES}
                or entry.filename in {f"{info}/licenses/LICENSE", f"{info}/licenses/NOTICE"}
            )
            if not allowed:
                raise ValueError(f"Unexpected wheel file: {entry.filename}")
            wheel_files[entry.filename] = archive.read(entry)
    source_files = {}
    with tarfile.open(directory / f"{stem}.tar.gz") as archive:
        for entry in archive.getmembers():
            _safe_path(entry.name)
            path = PurePosixPath(entry.name)
            if path.parts[0] != stem:
                raise ValueError(f"Unexpected source root: {entry.name}")
            if entry.isdir():
                continue
            name = path.relative_to(stem).as_posix()
            if not entry.isfile() or not _source_file(name):
                raise ValueError(f"Unexpected source file: {entry.name}")
            stream = archive.extractfile(entry)
            if stream is None:
                raise ValueError(f"Unreadable source file: {entry.name}")
            source_files[name] = stream.read()
    for name, content in modules.items():
        if wheel_files.get(name) != content or source_files.get(f"src/{name}") != content:
            raise ValueError(f"Missing or changed package module: {name}")
    for name in ("LICENSE", "NOTICE"):
        content = (project_root / name).read_bytes()
        if (
            wheel_files.get(f"{info}/licenses/{name}") != content
            or source_files.get(name) != content
        ):
            raise ValueError(f"Missing or changed license file: {name}")
    _metadata(wheel_files[f"{info}/METADATA"], project)
    _metadata(source_files["PKG-INFO"], project)
    entrypoints = configparser.ConfigParser()
    entrypoints.read_string(wheel_files[f"{info}/entry_points.txt"].decode())
    if entrypoints.get("console_scripts", "wos-mcp-bridge") != "wos_mcp_bridge.__main__:main":
        raise ValueError("Missing or invalid console entrypoint.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--project", type=Path, default=ROOT)
    parser.add_argument("--tag")
    arguments = parser.parse_args()
    try:
        check_distributions(arguments.directory, arguments.project, arguments.tag)
    except (
        ValueError,
        OSError,
        KeyError,
        configparser.Error,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        parser.exit(1, f"Distribution check failed: {exc}\n")
    print("PASS: wheel/source metadata, modules, licenses and archive contents")


if __name__ == "__main__":
    main()
