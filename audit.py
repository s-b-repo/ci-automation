#!/usr/bin/env python3
"""Inventory every repo the user owns: metadata, existing workflows, runs (7d).
Read-only. Writes audit.tsv + audit.json into the scratchpad."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

OWNER = "s-b-repo"
OUT = os.environ.get("OUT_DIR") or os.getcwd()
SINCE = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")


def gh(args, default=None):
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return default
        return r.stdout
    except Exception:
        return default


repos_raw = gh(["repo", "list", OWNER, "--limit", "300", "--json",
                "name,isFork,isArchived,isPrivate,primaryLanguage,defaultBranchRef,pushedAt"])
repos = json.loads(repos_raw) if repos_raw else []
repos.sort(key=lambda r: r["name"].lower())

rows = []
for r in repos:
    name = r["name"]
    lang = (r.get("primaryLanguage") or {}).get("name") or "-"
    db = (r.get("defaultBranchRef") or {}).get("name") or "-"
    wf_raw = gh(["api", f"repos/{OWNER}/{name}/contents/.github/workflows",
                 "--jq", "[.[].name]"])
    try:
        workflows = json.loads(wf_raw) if wf_raw else []
    except Exception:
        workflows = []
    runs_raw = gh(["api", f"repos/{OWNER}/{name}/actions/runs?per_page=50",
                   "--jq", ".workflow_runs"])
    try:
        runs = json.loads(runs_raw) if runs_raw else []
    except Exception:
        runs = []
    recent = [x for x in (runs or []) if (x.get("created_at") or "") >= SINCE]
    res = {}
    for x in recent:
        k = x.get("conclusion") or x.get("status") or "?"
        res[k] = res.get(k, 0) + 1
    row = {
        "name": name,
        "private": r["isPrivate"],
        "fork": r["isFork"],
        "archived": r["isArchived"],
        "language": lang,
        "default": db,
        "pushed": r.get("pushedAt", "-"),
        "workflows": workflows,
        "runs_7d": len(recent),
        "run_results": res,
    }
    rows.append(row)
    print(f"done: {name:40s} lang={lang:12s} wf={len(workflows)} runs7d={len(recent)}", flush=True)

with open(f"{OUT}/audit.json", "w") as f:
    json.dump(rows, f, indent=2)

with open(f"{OUT}/audit.tsv", "w") as f:
    f.write("repo\tpriv\tfork\tarch\tlang\tdefault\tworkflows\truns7d\tresults\n")
    for x in rows:
        f.write("\t".join([
            x["name"], "Y" if x["private"] else "n", "Y" if x["fork"] else "n",
            "Y" if x["archived"] else "n", x["language"], x["default"],
            ",".join(x["workflows"]) or "-", str(x["runs_7d"]),
            ",".join(f"{k}:{v}" for k, v in x["run_results"].items()) or "-",
        ]) + "\n")

print("AUDIT_COMPLETE", flush=True)
