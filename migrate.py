#!/usr/bin/env python3
"""Migrate repos from self-contained COPIED workflows to the central thin CALLERS
of ci-automation's reusable workflows:
  - release.yml  (ours = contains 'compute-release.sh')  -> _reuse-release caller
  - ci-automation.yml (ours; not already a caller)        -> _reuse-ci-<lang> caller
[skip ci] so nothing is re-released. Only swaps files that are ours.

Usage:  python3 migrate.py [--only a,b] [--go]   (default: dry-run)
Reads audit.json from the working dir (run audit.py first). Needs GH_TOKEN.
"""
import argparse, base64, json, os, subprocess, time

OWNER = "s-b-repo"
HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT_JSON = os.environ.get("AUDIT_JSON") or os.path.join(os.getcwd(), "audit.json")

with open(os.path.join(HERE, "templates/callers/release.yml"), encoding="utf-8") as _f:
    REL_CALLER = _f.read()
with open(os.path.join(HERE, "templates/callers/ci.yml"), encoding="utf-8") as _f:
    CI_CALLER = _f.read()

CI_REUSE = {'Rust':'_reuse-ci-rust.yml','Python':'_reuse-ci-python.yml',
    'JavaScript':'_reuse-ci-node.yml','TypeScript':'_reuse-ci-node.yml','Go':'_reuse-ci-go.yml',
    'C#':'_reuse-ci-dotnet.yml','Dart':'_reuse-ci-dart.yml','Shell':'_reuse-ci-shell.yml'}


def api(path, method="GET", fields=None):
    cmd = ["gh", "api", "--method", method, path]
    for k, v in (fields or {}).items():
        cmd += ["-f", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        return None, r.stderr.strip().split("\n")[-1][:90]
    try:
        return json.loads(r.stdout), None
    except json.JSONDecodeError:
        # Some endpoints legitimately return non-JSON (e.g. raw file bytes).
        # Only JSON decoding should fall through to the raw body here.
        return r.stdout, None


def swap(full, branch, path, must_contain, must_not_contain, new_text, go, counts, label):
    f, _ = api(f"repos/{full}/contents/{path}?ref={branch}")
    if f is None:
        return
    content = base64.b64decode(f["content"]).decode("utf-8", "replace")
    if must_contain not in content or must_not_contain in content:
        return  # not ours, or already a caller
    if not go:
        counts[f"would-{label}"] = counts.get(f"would-{label}", 0) + 1
        print(f"[plan {label}] {full.split('/')[-1]}", flush=True); return
    res, err = api(f"repos/{full}/contents/{path}", "PUT", {
        "message": f"ci: use central reusable {label} workflow [skip ci]",
        "content": base64.b64encode(new_text.encode()).decode(), "sha": f["sha"]})
    if res is None:
        counts[f"fail-{label}"] = counts.get(f"fail-{label}", 0) + 1
        print(f"[fail {label}] {full.split('/')[-1]}: {err}", flush=True)
    else:
        counts[label] = counts.get(label, 0) + 1
        print(f"[{label}] {full.split('/')[-1]}", flush=True); time.sleep(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only"); ap.add_argument("--go", action="store_true")
    a = ap.parse_args()
    with open(AUDIT_JSON, encoding="utf-8") as _f:
        rows = json.load(_f)
    if a.only:
        want = set(a.only.split(",")); rows = [r for r in rows if r["name"] in want]
    counts = {}
    for r in rows:
        name = r["name"]; full = f"{OWNER}/{name}"; branch = r.get("default") or "main"
        lang = r.get("language")
        swap(full, branch, ".github/workflows/release.yml",
             "compute-release.sh", "_reuse-release",
             REL_CALLER.replace("__BRANCH__", branch), a.go, counts, "release")
        if lang in CI_REUSE:
            ci = CI_CALLER.replace("__BRANCH__", branch).replace("__CIREUSE__", CI_REUSE[lang])
            swap(full, branch, ".github/workflows/ci-automation.yml",
                 "report-only", "_reuse-ci", ci, a.go, counts, "ci")
    print("\nSUMMARY:", counts, flush=True)


if __name__ == "__main__":
    main()
