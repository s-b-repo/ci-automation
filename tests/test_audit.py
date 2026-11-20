"""Tests for tools/audit.py.

The audit is a regression gate — it must flag the exact bug shapes removed
in the 2026-11 fix pass, and it must stay quiet on the current (fixed) tree
so CI keeps passing until a new bad pattern lands.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AUDIT = REPO / "tools" / "audit.py"


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(AUDIT), str(root)],
        capture_output=True, text=True, timeout=60,
    )


def test_clean_tree_passes() -> None:
    r = run(REPO)
    assert r.returncode == 0, r.stdout + r.stderr


def test_detects_workflow_injection_in_run_block(tmp_path: Path) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "bad.yml").write_text(
        "name: x\n"
        "on: [push]\n"
        "jobs:\n"
        "  j:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo ${{ inputs.args }}\n",
        encoding="utf-8",
    )
    r = run(tmp_path)
    assert r.returncode == 1
    assert "workflow-injection" in r.stdout


def test_allows_env_indirection(tmp_path: Path) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "good.yml").write_text(
        "name: x\n"
        "on: [push]\n"
        "jobs:\n"
        "  j:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: s\n"
        "        env:\n"
        "          A: ${{ inputs.args }}\n"
        "        run: echo \"$A\"\n",
        encoding="utf-8",
    )
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout


def test_detects_bare_except_exception(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(
        "try:\n    pass\nexcept Exception:\n    pass\n",
        encoding="utf-8",
    )
    r = run(tmp_path)
    assert r.returncode == 1
    assert "bare-except-Exception" in r.stdout


def test_detects_json_load_open(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(
        "import json\ndata = json.load(open('x.json'))\n",
        encoding="utf-8",
    )
    r = run(tmp_path)
    assert r.returncode == 1
    assert "json-load-open" in r.stdout


def test_detects_lstrip_v(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(
        "v = x.lstrip('v')\n", encoding="utf-8",
    )
    r = run(tmp_path)
    assert r.returncode == 1
    assert "lstrip-v-multiple" in r.stdout


def test_detects_subprocess_without_check(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(
        "import subprocess\n"
        "out = subprocess.run(['x'], capture_output=True, text=True).stdout\n",
        encoding="utf-8",
    )
    r = run(tmp_path)
    assert r.returncode == 1
    assert "subprocess-no-check" in r.stdout


def test_skips_tests_dir(tmp_path: Path) -> None:
    # Tests themselves are allowed to use bare `except Exception:` etc.
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "try:\n    pass\nexcept Exception:\n    pass\n",
        encoding="utf-8",
    )
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout
