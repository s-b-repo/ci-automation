"""Tests for release/compute-release.sh.

Covers: no-tag debut path, Conventional-Commit bump precedence, `type!:`
breaking marker, BREAKING CHANGE footer, force overrides, and the
[skip release] escape hatch.
"""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "release" / "compute-release.sh"


def git(cwd: Path, *args: str) -> str:
    env = {
        "HOME": str(cwd), "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@x",
        "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@x",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env,
    )
    return out.stdout


def commit(cwd: Path, subject: str, body: str = "") -> None:
    msg = subject + ("\n\n" + body if body else "")
    git(cwd, "commit", "-q", "--allow-empty", "-m", msg)


def tag(cwd: Path, name: str) -> None:
    git(cwd, "tag", name)


def run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=cwd, capture_output=True, text=True, timeout=30,
    )


def parse(stdout: str) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in stdout.splitlines() if "=" in line
    )


def setup(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    commit(repo, "initial commit")
    return repo


def test_debut_patch_without_feat(tmp_path: Path) -> None:
    repo = setup(tmp_path)
    r = run(repo)
    out = parse(r.stdout)
    assert r.returncode == 0, r.stderr
    assert out["next_version"] == "v0.1.0"
    assert out["bump"] == "patch"
    assert out["prev"] == "none"


def test_feat_bumps_minor_from_tag(tmp_path: Path) -> None:
    repo = setup(tmp_path)
    tag(repo, "v1.0.0")
    commit(repo, "feat: thing")
    r = run(repo)
    out = parse(r.stdout)
    assert out["next_version"] == "v1.1.0"
    assert out["bump"] == "minor"
    assert out["prev"] == "v1.0.0"


def test_bang_bumps_major(tmp_path: Path) -> None:
    repo = setup(tmp_path)
    tag(repo, "v1.2.3")
    commit(repo, "refactor!: rip out old api")
    r = run(repo)
    out = parse(r.stdout)
    assert out["next_version"] == "v2.0.0"
    assert out["bump"] == "major"


def test_breaking_change_footer_bumps_major(tmp_path: Path) -> None:
    repo = setup(tmp_path)
    tag(repo, "v1.0.0")
    commit(repo, "feat: thing", body="BREAKING CHANGE: ripped out old api")
    r = run(repo)
    out = parse(r.stdout)
    assert out["bump"] == "major"
    assert out["next_version"] == "v2.0.0"


def test_fix_only_bumps_patch(tmp_path: Path) -> None:
    repo = setup(tmp_path)
    tag(repo, "v0.5.0")
    commit(repo, "fix: thing")
    r = run(repo)
    out = parse(r.stdout)
    assert out["next_version"] == "v0.5.1"
    assert out["bump"] == "patch"


def test_skip_release_escape_hatch(tmp_path: Path) -> None:
    repo = setup(tmp_path)
    tag(repo, "v1.0.0")
    commit(repo, "feat: thing [skip release]")
    r = run(repo)
    out = parse(r.stdout)
    assert out["bump"] == "none"
    assert out["next_version"] == ""


def test_explicit_version_force(tmp_path: Path) -> None:
    repo = setup(tmp_path)
    tag(repo, "v1.0.0")
    commit(repo, "feat: thing")
    r = run(repo, "v9.9.9")
    out = parse(r.stdout)
    assert out["bump"] == "explicit"
    assert out["next_version"] == "v9.9.9"


def test_force_level(tmp_path: Path) -> None:
    repo = setup(tmp_path)
    tag(repo, "v1.0.0")
    commit(repo, "fix: thing")
    r = run(repo, "major")
    out = parse(r.stdout)
    assert out["bump"] == "major"
    assert out["next_version"] == "v2.0.0"


def test_invalid_force_rejected(tmp_path: Path) -> None:
    repo = setup(tmp_path)
    tag(repo, "v1.0.0")
    r = run(repo, "nonsense")
    assert r.returncode == 2
    assert "invalid force value" in r.stderr


def test_nonconventional_still_releases(tmp_path: Path) -> None:
    repo = setup(tmp_path)
    tag(repo, "v1.0.0")
    commit(repo, "just a random commit")
    r = run(repo)
    out = parse(r.stdout)
    assert out["next_version"] == "v1.0.1"
    assert out["bump"] == "patch"
