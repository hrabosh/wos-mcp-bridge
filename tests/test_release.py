"""Release checks exercise real distribution archives, including accidental leaks."""

import io
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[1] / "scripts" / "check_dist.py"


@pytest.fixture
def distributions(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="wos-mcp-bridge"\nversion="1.2.3"\n'
        'requires-python=">=3.12"\nlicense="Apache-2.0"\n'
        'dependencies=["httpx>=0.28.1,<0.29"]\n'
    )
    (project / "LICENSE").write_text("Synthetic license fixture\n")
    (project / "NOTICE").write_text("Synthetic notice fixture\n")
    source = project / "src" / "wos_mcp_bridge"
    source.mkdir(parents=True)
    modules = {
        name: b'"""Synthetic module."""\n'
        for name in ("__init__.py", "__main__.py", "server.py", "client.py")
    }
    for name, content in modules.items():
        (source / name).write_bytes(content)
    metadata = (
        b"Metadata-Version: 2.4\nName: wos-mcp-bridge\nVersion: 1.2.3\n"
        b"Requires-Python: >=3.12\nLicense-Expression: Apache-2.0\n"
        b"Requires-Dist: httpx>=0.28.1,<0.29\n\nSynthetic fixture.\n"
    )
    wheel = {f"wos_mcp_bridge/{name}": content for name, content in modules.items()}
    info = "wos_mcp_bridge-1.2.3.dist-info"
    wheel.update(
        {
            f"{info}/METADATA": metadata,
            f"{info}/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
            f"{info}/entry_points.txt": b"[console_scripts]\nwos-mcp-bridge = wos_mcp_bridge.__main__:main\n",
            f"{info}/RECORD": b"",
            f"{info}/licenses/LICENSE": (project / "LICENSE").read_bytes(),
            f"{info}/licenses/NOTICE": (project / "NOTICE").read_bytes(),
        }
    )
    sdist = {f"src/wos_mcp_bridge/{name}": content for name, content in modules.items()}
    sdist.update(
        {name: (project / name).read_bytes() for name in ("pyproject.toml", "LICENSE", "NOTICE")}
    )
    sdist["PKG-INFO"] = metadata
    sdist["setup.cfg"] = b"[egg_info]\ntag_build =\ntag_date = 0\n"
    output = tmp_path / "dist"
    output.mkdir()
    (output / ".gitignore").write_text("*\n")

    subprocess.run(["git", "init", "--quiet", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "add", "."], check=True)

    def run(*, wheel_extra=None, sdist_extra=None, omit=None, tag="v1.2.3", local_file=None):
        wheel_files = {**wheel, **(wheel_extra or {})}
        source_files = {**sdist, **(sdist_extra or {})}
        if local_file:
            content = b"SYNTHETIC_LOCAL_SECRET"
            path = project / local_file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            source_files[local_file] = content
            if local_file.startswith("src/wos_mcp_bridge/"):
                wheel_files[local_file.removeprefix("src/")] = content
        wheel_files.pop(omit, None)
        with zipfile.ZipFile(output / "wos_mcp_bridge-1.2.3-py3-none-any.whl", "w") as archive:
            for name, content in wheel_files.items():
                archive.writestr(name, content)
        with tarfile.open(output / "wos_mcp_bridge-1.2.3.tar.gz", "w:gz") as archive:
            for name, content in source_files.items():
                entry = tarfile.TarInfo(f"wos_mcp_bridge-1.2.3/{name}")
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))
        return subprocess.run(
            [sys.executable, str(CHECKER), str(output), "--project", str(project), "--tag", tag],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    return run


def test_valid_release_pair(distributions):
    result = distributions()
    assert result.returncode == 0, result.stderr


def test_release_tag_must_match_artifact_version(distributions):
    result = distributions(tag="v2.0.0")
    assert result.returncode != 0
    assert "tag" in result.stderr.lower()


@pytest.mark.parametrize("archive", ["wheel", "sdist"])
@pytest.mark.parametrize(
    "path", [".env.production", ".ruff_cache/cache", "src/wos_mcp_bridge/.env"]
)
def test_release_rejects_unintended_files(distributions, archive, path):
    result = distributions(**{f"{archive}_extra": {path: b"SYNTHETIC_SECRET"}})
    assert result.returncode != 0
    assert "unexpected" in result.stderr.lower()
    assert "SYNTHETIC_SECRET" not in result.stderr


def test_release_rejects_omitted_package_module(distributions):
    result = distributions(omit="wos_mcp_bridge/client.py")
    assert result.returncode != 0
    assert "client.py" in result.stderr


def test_release_rejects_inconsistent_source_metadata(distributions):
    result = distributions(sdist_extra={"PKG-INFO": b"Name: wos-mcp-bridge\nVersion: 9.9.9\n"})
    assert result.returncode != 0
    assert "metadata" in result.stderr.lower()


@pytest.mark.parametrize(
    "path",
    [
        "docs/local-credentials.md",
        "tests/local_credentials.py",
        "src/wos_mcp_bridge/local_settings.py",
    ],
)
def test_release_rejects_untracked_local_files(distributions, path):
    result = distributions(local_file=path)
    assert result.returncode != 0
    assert "unexpected" in result.stderr.lower()
    assert "SYNTHETIC_LOCAL_SECRET" not in result.stderr
