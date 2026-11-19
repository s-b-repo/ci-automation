# ci-automation

Reusable toolkit that adds **automated per-change SemVer releases** and
**free code-quality / bug / security CI** to any GitHub repo, and rolls it out
across many repos at once via pull requests.

Built for the `s-b-repo` account. Everything here uses **only free** GitHub
features (Actions on public repos, Dependabot, open-source actions). CodeQL is
added to **public repos only** — on private repos it needs paid GitHub Advanced
Security, so private repos get release + Dependabot + CI instead.

## What each repo gets

| File | Purpose |
|---|---|
| `.github/workflows/release.yml` + `.github/release/*` | Per-change SemVer release from Conventional Commits + categorized auto-notes |
| `.github/workflows/codeql.yml` | CodeQL static bug/security analysis (public repos, supported languages) |
| `.github/workflows/ci-automation.yml` | Language-aware lint (report-only) + build + test |
| `.github/dependabot.yml` | Dependency + security updates (deps ecosystem + github-actions) |

Release bump rules (highest wins since the last `vX.Y.Z` tag):
`feat!:`/`BREAKING CHANGE` → major, `feat:` → minor, everything else → patch.
`[skip release]` in a commit suppresses it.

## Usage

```bash
# 1. Inventory every repo (languages, existing workflows, 7-day run health)
python3 audit.py                      # writes audit.json / audit.tsv

# 2. Preview what would change (no writes)
python3 rollout.py --list
python3 rollout.py --list --lang Rust

# 3. Open the PRs
python3 rollout.py --go --only myrepo        # one repo
python3 rollout.py --go --lang Python        # a language batch
python3 rollout.py --go                       # everything legit
```

`rollout.py` is **idempotent and additive**: it only adds missing files, never
clobbers existing workflows, skips repos already covered, and opens one PR per
repo on branch `ci/add-automation`.

It **excludes** malware/abuse tooling (RATs, spyware, keyloggers, DoS/flood,
C2/botnets, cheats, credential/cookie stealers, spoofing/fraud) and skips
non-code repos (cheatsheets, dork lists, docs). Edit the `MAL` / `NONCODE`
patterns in `rollout.py` to adjust.

## Templates

`templates/` holds the source files. Placeholders injected per repo:
`__BRANCH__` (default branch), `__CRON__` (CodeQL schedule), `__MATRIX__`
(CodeQL language matrix).
