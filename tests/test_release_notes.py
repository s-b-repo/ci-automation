"""Tests for release/release-notes.py: git-failure surfacing, bot exclusion,
Conventional-Commit grouping, and release-chore skipping.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "release" / "release-notes.py"


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
        env={"GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@x",
             "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@x",
             "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
             "HOME": str(cwd), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    return out.stdout


def commit(cwd: Path, msg: str, author: str = "Dev <dev@x>") -> None:
    (cwd / "_").write_text(msg)
    name, email = author.split(" <")
    email = email.rstrip(">")
    subprocess.run(
        ["git", "add", "-A"], cwd=cwd, check=True,
        env={"HOME": str(cwd), "PATH": "/usr/bin:/bin"},
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", msg, "--allow-empty",
         f"--author={name} <{email}>"],
        cwd=cwd, check=True,
        env={"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
             "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
             "HOME": str(cwd), "PATH": "/usr/bin:/bin"},
    )


def run_script(cwd: Path, prev: str = "none") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), prev],
        cwd=cwd, capture_output=True, text=True, timeout=30,
    )


def setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    return repo


def test_fails_outside_git_repo(tmp_path: Path) -> None:
    r = run_script(tmp_path)
    assert r.returncode != 0
    assert "git log failed" in r.stdout + r.stderr


def test_groups_conventional_commits(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    commit(repo, "feat: add thing")
    commit(repo, "fix: patch bug")
    commit(repo, "docs: readme")
    commit(repo, "random one-liner no type")
    r = run_script(repo, "none")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "Features" in out and "add thing" in out
    assert "Fixes" in out and "patch bug" in out
    assert "Documentation" in out and "readme" in out
    assert "Other changes" in out and "random one-liner" in out


def test_excludes_bot_authors(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    commit(repo, "feat: human feature")
    commit(repo, "chore: bot thing",
           author="github-actions[bot] <bot@x>")
    commit(repo, "fix: dep bump", author="dependabot[bot] <bot@x>")
    r = run_script(repo, "none")
    assert r.returncode == 0, r.stderr
    assert "human feature" in r.stdout
    assert "bot thing" not in r.stdout
    assert "dep bump" not in r.stdout


def test_excludes_release_chore(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    commit(repo, "feat: x")
    commit(repo, "chore(release): v1.2.3 [skip ci]")
    r = run_script(repo, "none")
    assert r.returncode == 0, r.stderr
    assert "v1.2.3" not in r.stdout


def test_breaking_change_bang(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    commit(repo, "feat!: drop old api")
    r = run_script(repo, "none")
    assert r.returncode == 0, r.stderr
    assert "BREAKING CHANGES" in r.stdout
    assert "drop old api" in r.stdout


def test_no_commits_since_prev_prints_notice(tmp_path: Path) -> None:
    # The graceful path: prev tag exists, nothing has landed since. The script
    # must not fail the workflow — a quiet "no notable changes" release body
    # is acceptable, a nonzero exit would block the tag.
    repo = setup_repo(tmp_path)
    commit(repo, "feat: initial")
    subprocess.run(
        ["git", "tag", "v1.0.0"], cwd=repo, check=True,
        env={"HOME": str(repo), "PATH": "/usr/bin:/bin"},
    )
    r = run_script(repo, "v1.0.0")
    assert r.returncode == 0, r.stderr
    assert "No notable changes" in r.stdout
