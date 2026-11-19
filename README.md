# ci-automation

Central management for all of `s-b-repo`'s GitHub "workers" (workflows):
**automated per-change SemVer releases** + **free code-quality / bug / security
CI**, rolled out and kept in sync across every repo.

Everything here uses **only free** GitHub features (Actions on public repos,
Dependabot, open-source actions). CodeQL is added to **public repos only** — on
private repos it needs paid Advanced Security, so private repos get release +
Dependabot + CI instead.

## How it's organized

```
.github/workflows/
  _reuse-release.yml   # reusable release logic (workflow_call) — edit once, every repo benefits
  _reuse-codeql.yml    # reusable CodeQL analysis (workflow_call)
  sync-workers.yml     # the management bot: re-scans all repos, opens update PRs
release/               # compute-release.sh, bump-manifest.py, release-notes.py (used by _reuse-release)
templates/
  callers/             # thin caller workflows each repo gets (call the _reuse-* above)
  ci/                  # language CI (copied into repos): rust/python/node/go/dotnet/dart/shell
audit.py rollout.py
```

**Reusable workflows** are the key idea: each repo's `release.yml` / `codeql.yml`
is a 10-line *caller* that does `uses: s-b-repo/ci-automation/.github/workflows/_reuse-*.yml@main`.
Improving release or CodeQL behaviour = editing one file here; all repos pick it
up automatically.

## The sync bot

`sync-workers.yml` runs `audit.py` (inventory every repo) then `rollout.py`
(open/refresh PRs). Trigger it from the Actions tab (**Run workflow**) with args
like `--go --lang Rust`, or let the weekly schedule report drift (dry-run by
default).

It needs a secret **`ROLLOUT_TOKEN`** — a PAT with `repo` + `workflow` scope
(classic), or a fine-grained PAT with Contents:write + Pull requests:write +
Workflows:write on the target repos. `GITHUB_TOKEN` cannot reach other repos.

```bash
gh secret set ROLLOUT_TOKEN -R s-b-repo/ci-automation --body '<your_pat>'
```

## Run it locally instead

```bash
python3 audit.py                 # writes audit.json / audit.tsv
python3 rollout.py --list        # preview
python3 rollout.py --go --lang Python   # open PRs for a language
python3 rollout.py --go          # everything legit
```

`rollout.py` is idempotent/additive: only adds missing files, never clobbers
existing workflows, one PR per repo on branch `ci/add-automation`. It **excludes**
malware/abuse tooling (RATs, spyware, keyloggers, DoS/flood, C2/botnets, cheats,
credential/cookie stealers, spoofing/fraud) and skips non-code repos. Adjust the
`MAL` / `NONCODE` patterns in `rollout.py`.

## Release bump rules

Highest wins since the last `vX.Y.Z` tag: `feat!:`/`BREAKING CHANGE` → major,
`feat:` → minor, everything else → patch. `[skip release]` suppresses.
