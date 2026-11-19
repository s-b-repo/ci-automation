#!/usr/bin/env python3
"""Roll out release + quality/security workflows to s-b-repo code repos.

Idempotent & additive: never clobbers existing workflows, only adds missing
files, opens one PR per repo on branch `ci/add-automation`. Skips repos that
are already covered, empty, or that already have an open automation PR.

Usage:
  rollout.py --list                 # show planned actions, no changes
  rollout.py --lang Rust            # only Rust repos
  rollout.py --only a,b,c           # only these repos
  rollout.py --limit 3              # cap count (for testing)
  rollout.py --go                   # actually open PRs (default is dry-run)
"""
import argparse, base64, hashlib, json, re, subprocess, sys, time

OWNER = "s-b-repo"
SP = "/tmp/claude-1000/-home-kali-Documents-camio/75ab06db-f124-4537-80bf-bb53bee623b9/scratchpad"
TPL = f"{SP}/templates"
BRANCH = "ci/add-automation"

BACKUP = re.compile(r'(backup|-mirror|mirror-|snapshot|redux|darkclove)', re.I)
MAL = re.compile(r'(rat|spyware|keylog|cred-?steal|cookie-grabber|ddos|dos-tool|slowloris|'
    r'mirai|annihilator|botnet|aimbot|aimnix|cheat|antiaim|deadlocked|eac-|system32-remover|'
    r'skibbidi|discord.*break|break.*discord|ppcord-bug|spoofing|number-gen|nuber-gen|'
    r'account-gen|coupon-dorker|mullvad.*gen|embybypass|hypnogoon|basic-iphone|ransom|'
    r'ps1ware|packer$|dmarc-spoof|supercookie|exfil|skiddedsql|api-dos|next.js-dos|comkid|'
    r'lfd-down|dstat|FBI-NET|iloveproxies|whyabuse|badpython|badadmin)', re.I)
NONCODE = re.compile(r'(cheat ?sheet|cheatsheet|guide|dorks|awesome|list|-docs|docs$|'
    r'hosting-list|blocklist|readme|\.github\.io|^s-b-repo$|^a-ideas$|^test$|materials|'
    r'trick-book|learning|collection|examples$|-example|notes|comparison|intune|'
    r'shaders|nvidea-guide|community$|community-edition)', re.I)

CODEQL = {'Python':('python','none'),'JavaScript':('javascript-typescript','none'),
    'TypeScript':('javascript-typescript','none'),'Go':('go','none'),'C#':('csharp','none'),
    'Java':('java-kotlin','none'),'C':('c-cpp','autobuild'),'C++':('c-cpp','autobuild')}
CI_TPL = {'Rust':'rust.yml','Python':'python.yml','JavaScript':'node.yml','TypeScript':'node.yml',
    'Go':'go.yml','C#':'dotnet.yml','Dart':'dart.yml','Shell':'shell.yml'}
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
            if ("secondary rate limit" in e.lower() or "rate limit" in e.lower()) and attempt < 3:
                time.sleep(60)
                continue
            return None, e
        try:
            return json.loads(out), None
        except Exception:
            return out, None
    return None, "rate-limited after retries"


def is_source(r):
    return not r["fork"] and not r["archived"] and not BACKUP.search(r["name"])

def is_legit(r):
    n = r["name"]
    if MAL.search(n): return False
    if r["language"] in (None, "-"): return False
    if r["language"] in (None,"-","HTML","Markdown","CSS","Batchfile") and NONCODE.search(n): return False
    if NONCODE.search(n) and r["language"] in (None,"-","HTML","Markdown","CSS","Batchfile"): return False
    return True


def read_tpl(rel):
    with open(f"{TPL}/{rel}", "rb") as f:
        return f.read()


def cron_for(name):
    h = int(hashlib.sha1(name.encode()).hexdigest(), 16)
    return f"{h % 60} {h // 60 % 24} * * {h // 1440 % 7}"


def build_files(repo, branch, root_names, wf_names, dotgh_names):
    """Return {path: bytes} of files to ADD (missing only)."""
    lang = repo["language"]
    name = repo["name"]
    files = {}
    br = branch

    has_release = "release.yml" in wf_names
    has_codeql = any("codeql" in w for w in wf_names)
    has_ci = bool(EXISTING_CI & set(wf_names))
    has_dependabot = "dependabot.yml" in dotgh_names

    # --- release automation set ---
    if not has_release:
        rel = read_tpl("common/.github/workflows/release.yml").decode().replace("__BRANCH__", br)
        files[".github/workflows/release.yml"] = rel.encode()
        files[".github/release.yml"] = read_tpl("common/.github/release.yml")
        for s in ("compute-release.sh", "bump-manifest.py", "release-notes.py"):
            files[f".github/release/{s}"] = read_tpl(f"common/.github/release/{s}")

    # --- CodeQL --- (free only on PUBLIC repos; private needs paid Advanced Security)
    if not has_codeql and lang in CODEQL and not repo["private"]:
        cq_lang, mode = CODEQL[lang]
        matrix = f"          - language: {cq_lang}\n            build-mode: {mode}\n"
        cq = (read_tpl("codeql/codeql.yml").decode()
              .replace("__BRANCH__", br).replace("__CRON__", cron_for(name))
              .replace("__MATRIX__", matrix))
        files[".github/workflows/codeql.yml"] = cq.encode()

    # --- language CI ---
    if not has_ci and lang in CI_TPL:
        ci = read_tpl(f"ci/{CI_TPL[lang]}").decode().replace("__BRANCH__", br)
        files[".github/workflows/ci-automation.yml"] = ci.encode()

    # --- dependabot ---
    if not has_dependabot:
        blocks = ['  - package-ecosystem: "github-actions"\n    directory: "/"\n    schedule:\n      interval: "weekly"\n']
        if lang in ECO:
            eco, manifests = ECO[lang]
            if any(m in root_names for m in manifests):
                blocks.append(f'  - package-ecosystem: "{eco}"\n    directory: "/"\n    schedule:\n      interval: "weekly"\n    open-pull-requests-limit: 5\n')
        files[".github/dependabot.yml"] = ("version: 2\nupdates:\n" + "".join(blocks)).encode()

    return files


def list_names(path):
    data, err = gj(path)
    if data is None or not isinstance(data, list):
        return set()
    return {e["name"] for e in data}


def process(repo, go):
    name = repo["name"]
    full = f"{OWNER}/{name}"
    branch = repo["default"]
    if not branch or branch == "-":
        return "skip", "no default branch (empty repo)"

    # existing content
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

    # already have the branch / PR?
    ref, _ = gj(f"repos/{full}/git/ref/heads/{branch}")
    if ref is None:
        return "fail", "cannot read base ref"
    base_sha = ref["object"]["sha"]
    existing_branch, _ = gj(f"repos/{full}/git/ref/heads/{BRANCH}")
    if existing_branch is not None:
        prs, _ = gj(f"repos/{full}/pulls?head={OWNER}:{BRANCH}&state=open")
        if prs:
            return "skip", f"PR already open: {prs[0]['html_url']}"
        return "skip", "automation branch already exists"

    base_commit, _ = gj(f"repos/{full}/git/commits/{base_sha}")
    base_tree = base_commit["tree"]["sha"]

    tree = []
    for path, content in files.items():
        blob, err = gj(f"repos/{full}/git/blobs", "POST",
                       {"content": base64.b64encode(content).decode(), "encoding": "base64"})
        if blob is None:
            return "fail", f"blob {path}: {err}"
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})

    newtree, err = gj(f"repos/{full}/git/trees", "POST", {"base_tree": base_tree, "tree": tree})
    if newtree is None:
        return "fail", f"tree: {err}"
    msg = "ci: add automated releases + quality/security workflows\n\nAdds per-change SemVer releases, CodeQL, Dependabot and language CI."
    commit, err = gj(f"repos/{full}/git/commits", "POST",
                     {"message": msg, "tree": newtree["sha"], "parents": [base_sha]})
    if commit is None:
        return "fail", f"commit: {err}"
    newref, err = gj(f"repos/{full}/git/refs", "POST",
                     {"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})
    if newref is None:
        return "fail", f"ref: {err}"

    body = ("Automated rollout of release + quality/security workflows.\n\n"
            "- **Release**: per-change SemVer from Conventional Commits + auto notes "
            "(`.github/workflows/release.yml`, `.github/release/`)\n"
            "- **CodeQL**: static bug/security analysis (where supported)\n"
            "- **Dependabot**: dependency + security updates\n"
            "- **CI**: language-aware lint (report-only) + build + test\n\n"
            "Lint/format are non-blocking; build/test are the gate. "
            "Merging activates automated releases on pushes to this branch.")
    pr, err = gj(f"repos/{full}/pulls", "POST",
                 {"title": "ci: add automated releases + quality/security workflows",
                  "head": BRANCH, "base": branch, "body": body})
    if pr is None:
        return "fail", f"pr: {err}"
    return "pr", pr["html_url"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang"); ap.add_argument("--only"); ap.add_argument("--limit", type=int)
    ap.add_argument("--go", action="store_true"); ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    rows = json.load(open(f"{SP}/audit.json"))
    repos = [r for r in rows if is_source(r) and is_legit(r)]
    if a.lang:
        repos = [r for r in repos if (r["language"] or "") == a.lang]
    if a.only:
        want = set(a.only.split(","))
        repos = [r for r in repos if r["name"] in want]
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
