#!/usr/bin/env python3
"""Roll out / update release + quality/security workers across s-b-repo repos.

Emits thin CALLER workflows that invoke the reusable workflows in this repo
(_reuse-release.yml, _reuse-codeql.yml) plus a copied language CI and Dependabot.
Idempotent & additive: only adds missing files, never clobbers existing
workflows, opens one PR per repo on branch `ci/add-automation`.

Usage:
  rollout.py --list                 # dry-run (plan only)
  rollout.py --lang Rust            # filter by language
  rollout.py --only a,b,c           # specific repos
  rollout.py --limit 3              # cap (testing)
  rollout.py --go                   # actually open PRs

Requires GH_TOKEN (PAT with repo+workflow scope) when run in Actions.
Reads audit.json from the working dir (run audit.py first).
"""
import argparse, base64, hashlib, json, os, re, subprocess, time

OWNER = "s-b-repo"
HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, "templates")
AUDIT_JSON = os.environ.get("AUDIT_JSON") or os.path.join(os.getcwd(), "audit.json")
BRANCH = "ci/add-automation"

BACKUP = re.compile(r'(backup|-mirror|mirror-|snapshot|redux|darkclove)', re.I)
MAL = re.compile(r'(rat|spyware|keylog|cred-?steal|cookie-grabber|ddos|dos-tool|slowloris|'
    r'mirai|annihilator|botnet|aimbot|aimnix|cheat|antiaim|deadlocked|eac-|system32-remover|'
    r'skibbidi|discord.*break|break.*discord|ppcord-bug|spoofing|number-gen|nuber-gen|'
    r'account-gen|coupon-dorker|mullvad.*gen|embybypass|hypnogoon|basic-iphone|ransom|'
    r'ps1ware|packer$|dmarc-spoof|supercookie|exfil|skiddedsql|api-dos|next.js-dos|comkid|'
    r'lfd-down|dstat|FBI-NET|iloveproxies|whyabuse|badpython|badadmin|flood|deadshot|'
    r'[-_]c2($|[-_])|[-_]c3($|[-_])|firec2|snowc2|c2-panel)', re.I)
NONCODE = re.compile(r'(cheat ?sheet|cheatsheet|guide|dorks|awesome|list|-docs|docs$|'
    r'hosting-list|blocklist|readme|\.github\.io|^s-b-repo$|^a-ideas$|^test$|materials|'
    r'trick-book|learning|collection|examples$|-example|notes|comparison|intune|'
    r'shaders|nvidea-guide|community$|community-edition)', re.I)

# CodeQL only where build-mode:none works (C/C++ needs a build -> excluded).
CODEQL = {'Python':('python','none'),'JavaScript':('javascript-typescript','none'),
    'TypeScript':('javascript-typescript','none'),'Go':('go','none'),'C#':('csharp','none'),
    'Java':('java-kotlin','none')}
CI_REUSE = {'Rust':'_reuse-ci-rust.yml','Python':'_reuse-ci-python.yml',
    'JavaScript':'_reuse-ci-node.yml','TypeScript':'_reuse-ci-node.yml','Go':'_reuse-ci-go.yml',
    'C#':'_reuse-ci-dotnet.yml','Dart':'_reuse-ci-dart.yml','Shell':'_reuse-ci-shell.yml',
    'C':'_reuse-ci-cpp.yml','C++':'_reuse-ci-cpp.yml'}
ECO = {'Rust':('cargo',('Cargo.toml',)),'Python':('pip',('requirements.txt','pyproject.toml','setup.py','setup.cfg')),
    'JavaScript':('npm',('package.json',)),'TypeScript':('npm',('package.json',)),
    'Go':('gomod',('go.mod',)),'C#':('nuget',('packages.config',)),'Dart':('pub',('pubspec.yaml',))}
EXISTING_CI = {'ci.yml','ci-automation.yml','rust.yml','node.yml','go.yml','dotnet.yml',
    'python.yml','build.yml','test.yml','tests.yml','quality.yml','lint.yml','main.yml','pipeline.yml'}


def gh(path, method="GET", payload=None):
    cmd = ["gh", "api", "--method", method, path]
    inp = None
    if payload is not None:
        cmd += ["--input", "-"]; inp = json.dumps(payload)
    r = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=90)
    return r.returncode, r.stdout, r.stderr


def gj(path, method="GET", payload=None):
    for attempt in range(4):
        rc, out, err = gh(path, method, payload)
        if rc != 0:
            e = err.strip()
            if "rate limit" in e.lower() and attempt < 3:
                time.sleep(60); continue
            return None, e
        try:
            return json.loads(out), None
        except json.JSONDecodeError:
            # Not every endpoint returns JSON — return the raw body so callers
            # that don't need JSON still work.
            return out, None
    return None, "rate-limited after retries"


def ref_state(path):
    """Tri-state ref probe: 'exists' | 'missing' | 'error:<msg>'.

    gh api emits 'HTTP 404' on stderr when a ref does not exist. The plain gj
    wrapper conflates that with any other failure, so a transient error would
    make the rollout think the PR branch does not exist and then try to create
    it, which later fails. This separates the two.
    """
    rc, _out, err = gh(path)
    if rc == 0:
        return "exists"
    if "HTTP 404" in err or "Not Found" in err:
        return "missing"
    return f"error:{err.strip().splitlines()[-1:] or ''}"


def is_source(r):
    return not r["fork"] and not r["archived"] and not BACKUP.search(r["name"])

def is_legit(r):
    n = r["name"]
    if MAL.search(n): return False
    if r["language"] in (None, "-"): return False
    if NONCODE.search(n) and r["language"] in (None,"-","HTML","Markdown","CSS","Batchfile"):
        return False
    return True


def read_tpl(rel):
    with open(os.path.join(TPL, rel), "rb") as f:
        return f.read()


def cron_for(name):
    # Deterministic bucketing of repo name -> cron slot; not security.
    h = int(hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest(), 16)
    return f"{h % 60} {h // 60 % 24} * * {h // 1440 % 7}"


def build_files(repo, branch, root_names, wf_names, dotgh_names):
    lang = repo["language"]; name = repo["name"]; files = {}; br = branch
    wf_lower = {w.lower() for w in wf_names}
    dotgh_lower = {w.lower() for w in dotgh_names}
    has_release = "release.yml" in wf_lower
    has_codeql = any("codeql" in w for w in wf_lower)
    has_ci = bool(EXISTING_CI & wf_lower)
    has_dependabot = "dependabot.yml" in dotgh_lower

    if not has_release:
        files[".github/workflows/release.yml"] = \
            read_tpl("callers/release.yml").decode().replace("__BRANCH__", br).encode()

    if not has_codeql and lang in CODEQL and not repo["private"]:
        cq_lang, mode = CODEQL[lang]
        cq = (read_tpl("callers/codeql.yml").decode()
              .replace("__BRANCH__", br).replace("__CRON__", cron_for(name))
              .replace("__CQLANG__", cq_lang).replace("__CQMODE__", mode))
        files[".github/workflows/codeql.yml"] = cq.encode()

    if not has_ci and lang in CI_REUSE:
        ci = (read_tpl("callers/ci.yml").decode()
              .replace("__BRANCH__", br).replace("__CIREUSE__", CI_REUSE[lang]))
        files[".github/workflows/ci-automation.yml"] = ci.encode()

    if not has_dependabot:
        blocks = ['  - package-ecosystem: "github-actions"\n    directory: "/"\n    schedule:\n      interval: "weekly"\n']
        if lang in ECO:
            eco, manifests = ECO[lang]
            if any(m in root_names for m in manifests):
                blocks.append(f'  - package-ecosystem: "{eco}"\n    directory: "/"\n    schedule:\n      interval: "weekly"\n    open-pull-requests-limit: 5\n')
        files[".github/dependabot.yml"] = ("version: 2\nupdates:\n" + "".join(blocks)).encode()

    return files


def list_names(path):
    data, _ = gj(path)
    return {e["name"] for e in data} if isinstance(data, list) else set()


def process(repo, go):
    name = repo["name"]; full = f"{OWNER}/{name}"; branch = repo["default"]
    if not branch or branch == "-":
        return "skip", "no default branch"
    wf_names = list_names(f"repos/{full}/contents/.github/workflows")
    dotgh_names = list_names(f"repos/{full}/contents/.github")
    root_names = list_names(f"repos/{full}/contents")
    if not root_names:
        return "skip", "empty repo"
    files = build_files(repo, branch, root_names, wf_names, dotgh_names)
    if not files:
        return "skip", "already covered"
    plan = ", ".join(sorted(p.split("/")[-1] for p in files))
    if not go:
        return "plan", f"{len(files)} files: {plan}"

    ref, _ = gj(f"repos/{full}/git/ref/heads/{branch}")
    if ref is None:
        return "fail", "cannot read base ref"
    base_sha = ref["object"]["sha"]
    state = ref_state(f"repos/{full}/git/ref/heads/{BRANCH}")
    if state == "exists":
        prs, _ = gj(f"repos/{full}/pulls?head={OWNER}:{BRANCH}&state=open")
        return "skip", (f"PR open: {prs[0]['html_url']}" if prs else "branch exists")
    if state.startswith("error:"):
        # Don't proceed to create a tree/commit on uncertain state — a later
        # ref-create would fail anyway, and we might paper over a real outage.
        return "fail", f"cannot probe rollout branch: {state[6:]}"
    base_commit, _ = gj(f"repos/{full}/git/commits/{base_sha}")
    tree = []
    for path, content in files.items():
        blob, err = gj(f"repos/{full}/git/blobs", "POST",
                       {"content": base64.b64encode(content).decode(), "encoding": "base64"})
        if blob is None:
            return "fail", f"blob {path}: {err}"
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    nt, err = gj(f"repos/{full}/git/trees", "POST",
                 {"base_tree": base_commit["tree"]["sha"], "tree": tree})
    if nt is None:
        return "fail", f"tree: {err}"
    msg = "ci: add automated releases + quality/security workflows"
    c, err = gj(f"repos/{full}/git/commits", "POST",
                {"message": msg, "tree": nt["sha"], "parents": [base_sha]})
    if c is None:
        return "fail", f"commit: {err}"
    _ref, ref_err = gj(f"repos/{full}/git/refs", "POST",
                       {"ref": f"refs/heads/{BRANCH}", "sha": c["sha"]})
    if _ref is None:
        return "fail", f"ref create: {ref_err}"
    body = ("Automated rollout of release + quality/security workers (thin callers of "
            "s-b-repo/ci-automation reusable workflows).\n\n"
            "- Release: per-change SemVer + auto notes\n- CodeQL (public repos)\n"
            "- Dependabot\n- Language CI (lint report-only; build/test gate)\n\n"
            "Merging activates automated releases on pushes to the default branch.")
    pr, err = gj(f"repos/{full}/pulls", "POST",
                 {"title": "ci: add automated releases + quality/security workflows",
                  "head": BRANCH, "base": branch, "body": body})
    return ("pr", pr["html_url"]) if pr else ("fail", f"pr: {err}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang"); ap.add_argument("--only"); ap.add_argument("--limit", type=int)
    ap.add_argument("--go", action="store_true"); ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    with open(AUDIT_JSON, encoding="utf-8") as _f:
        rows = json.load(_f)
    repos = [r for r in rows if is_source(r) and is_legit(r)]
    if a.lang:
        repos = [r for r in repos if (r["language"] or "") == a.lang]
    if a.only:
        want = set(a.only.split(",")); repos = [r for r in repos if r["name"] in want]
    repos.sort(key=lambda r: r["name"].lower())
    if a.limit:
        repos = repos[:a.limit]
    go = a.go and not a.list
    print(f"{'DRY-RUN' if not go else 'EXECUTING'} over {len(repos)} repos\n", flush=True)
    counts = {}
    for r in repos:
        try:
            status, detail = process(r, go)
        except Exception as e:
            status, detail = "fail", f"exception: {e}"
        counts[status] = counts.get(status, 0) + 1
        print(f"[{status:5s}] {r['name']:40s} {detail}", flush=True)
        if go and status in ("pr", "fail"):
            time.sleep(6)
    print("\nSUMMARY:", counts, flush=True)


if __name__ == "__main__":
    main()
