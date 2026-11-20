"""Tests for release/bump-manifest.py.

Covers the two regressions from the 2026-11 audit — Dart pubspec build-number
preservation and single-v stripping — plus the already-correct cases so a
future rewrite doesn't lose them.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "release" / "bump-manifest.py"


def run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args],
        cwd=cwd, capture_output=True, text=True, timeout=30,
    )


def test_pubspec_preserves_build_number(tmp_path: Path) -> None:
    (tmp_path / "pubspec.yaml").write_text(
        "name: app\nversion: 1.0.0+5\n", encoding="utf-8",
    )
    r = run(tmp_path, "2.0.0")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "pubspec.yaml").read_text() == "name: app\nversion: 2.0.0+5\n"


def test_pubspec_without_build_bumps_plain(tmp_path: Path) -> None:
    (tmp_path / "pubspec.yaml").write_text(
        "name: app\nversion: 1.0.0\n", encoding="utf-8",
    )
    r = run(tmp_path, "2.1.3")
    assert r.returncode == 0, r.stderr
    assert "version: 2.1.3\n" in (tmp_path / "pubspec.yaml").read_text()


def test_rejects_double_v(tmp_path: Path) -> None:
    r = run(tmp_path, "vv1.2.3")
    assert r.returncode != 0
    assert "not a plain X.Y.Z" in (r.stderr + r.stdout)


def test_single_v_is_stripped(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("0.0.0\n", encoding="utf-8")
    r = run(tmp_path, "v1.2.3")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "VERSION").read_text() == "1.2.3\n"


def test_malformed_package_json_warns(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
    r = run(tmp_path, "1.2.3")
    assert r.returncode == 0  # best-effort by design
    assert "warning: package.json" in r.stderr


def test_package_json_bump(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "x", "version": "0.1.0"}, indent=2) + "\n",
        encoding="utf-8",
    )
    r = run(tmp_path, "0.2.0")
    assert r.returncode == 0, r.stderr
    data = json.loads((tmp_path / "package.json").read_text())
    assert data["version"] == "0.2.0"


def test_cargo_pins_pathdeps_to_new_major(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "x"\nversion = "0.9.0"\n\n'
        '[dependencies]\n'
        'inner = { path = "inner", version = "0.9" }\n'
        'external = { version = "1.2.3" }\n',
        encoding="utf-8",
    )
    r = run(tmp_path, "1.0.0")
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "Cargo.toml").read_text()
    assert 'version = "1.0.0"' in text        # package version
    assert 'path = "inner", version = "1"' in text  # path-dep re-pinned to new major
    assert 'external = { version = "1.2.3" }' in text  # external untouched


def test_no_change_when_already_at_version(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    mtime_before = (tmp_path / "VERSION").stat().st_mtime_ns
    r = run(tmp_path, "1.2.3")
    assert r.returncode == 0, r.stderr
    # No stdout means no files reported as changed.
    assert r.stdout.strip() == ""


def test_pyproject_toml_bump(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n\n'
        '[tool.other]\nversion = "7.7.7"\n',
        encoding="utf-8",
    )
    r = run(tmp_path, "0.2.0")
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "pyproject.toml").read_text()
    assert '[project]\nname = "x"\nversion = "0.2.0"' in text
    # Other tables must not be touched.
    assert '[tool.other]\nversion = "7.7.7"' in text
