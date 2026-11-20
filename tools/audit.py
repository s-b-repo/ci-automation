#!/usr/bin/env python3
"""Regex-based bug-class audit for this repo.

Runs the same categories the 2026-11 audit used (error_swallowing,
injection_and_eval, resource_leaks, workflow_injection) and exits non-zero on
any hit, so a regression is a CI failure instead of a review-time catch.

Patterns are deliberately narrow — they match the specific bad shapes we
removed, not every API call that happens to be in the same family. Each rule
has a `note` explaining what fires it and what the safe shape looks like, so
the failure message is actionable.
"""
import pathlib
import re
import sys
from collections.abc import Callable

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()


class Rule:
    __slots__ = ("name", "pattern", "note", "where", "extra_check")

    def __init__(
        self,
        name: str,
        pattern: str,
        note: str,
        where: Callable[[pathlib.Path], bool],
        extra_check: Callable[[str, re.Match[str]], bool] | None = None,
    ):
        self.name = name
        self.pattern = re.compile(pattern, re.M)
        self.note = note
        self.where = where
        self.extra_check = extra_check


def is_python(p: pathlib.Path) -> bool:
    posix = p.as_posix()
    # Skip tests (they're allowed to use bad shapes for coverage) and skip
    # the audit tool itself (its rule descriptions quote the bad shapes).
    if "tests/" in posix or posix.endswith("tools/audit.py"):
        return False
    return p.suffix == ".py"


def is_yaml_workflow(p: pathlib.Path) -> bool:
    return p.suffix in (".yml", ".yaml") and ".github/workflows" in p.as_posix()


def is_shell(p: pathlib.Path) -> bool:
    return p.suffix == ".sh"


_STEP_KEY = re.compile(
    r"(?:run|env|with|if|name|uses|id|shell|working-directory|"
    r"continue-on-error|timeout-minutes):"
)


def in_run_block(text: str, m: re.Match[str]) -> bool:
    """True iff the match sits in a run: block (unsafe shell context).

    Handles both the folded form (`run: |` followed by indented shell) and
    the inline form (`run: echo foo`), and treats matches inside an env:
    mapping as safe.
    """
    # Inline run: key and ${{ }} on the same line.
    line_start = text.rfind("\n", 0, m.start()) + 1
    this_line = text[line_start : text.find("\n", m.start()) if text.find("\n", m.start()) != -1 else len(text)]
    if re.match(r"\s*-?\s*run\s*:\s*\S", this_line) and "- run" in this_line or \
       re.search(r"(?:^|\s)run\s*:\s*[^|>\s]", this_line):
        return True
    # Folded run: walk back to the nearest step-level key.
    before = text[:line_start]
    for line in reversed(before.splitlines()):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if _STEP_KEY.match(stripped):
            return stripped.startswith("run:")
    return False


RULES: list[Rule] = [
    Rule(
        "workflow-injection",
        r"\$\{\{\s*(?:inputs|github\.event|secrets)\.[^}]+\}\}",
        "GitHub expands ${{ }} before the shell sees the line, so user-\n"
        "controlled text inside a run: block is a command-injection vector.\n"
        "Pass it through env: instead and dereference as $VAR.",
        where=is_yaml_workflow,
        extra_check=in_run_block,
    ),
    Rule(
        "untracked-steps-output-in-run",
        r"\$\{\{\s*steps\.[A-Za-z_][\w-]*\.outputs\.[^}]+\}\}",
        "Step outputs are also attacker-reachable when a prior step reflects\n"
        "untrusted data. Route through env: like inputs/secrets.",
        where=is_yaml_workflow,
        extra_check=in_run_block,
    ),
    Rule(
        "bare-open-read",
        r"^\s*\w+\s*=\s*open\s*\([^)]+\)\.read\s*\(\s*\)",
        "Leaks a file handle; use `with open(...) as f: data = f.read()`.",
        where=is_python,
    ),
    Rule(
        "json-load-open",
        r"\bjson\.load\s*\(\s*open\s*\(",
        "Leaks the file handle; use `with open(...) as f: json.load(f)`.",
        where=is_python,
    ),
    Rule(
        "bare-except-Exception",
        r"^\s*except\s+Exception\s*:\s*$",
        "Broad `except Exception:` swallows unrelated failures. Narrow to the\n"
        "specific exception you can actually handle (JSONDecodeError, OSError,\n"
        "subprocess.TimeoutExpired, ...), or use `except Exception as e:` and\n"
        "log `e` before continuing.",
        where=is_python,
    ),
    Rule(
        "subprocess-no-check",
        r"subprocess\.run\s*\([^)]*capture_output\s*=\s*True[^)]*\)\s*\.(stdout|stderr)",
        "subprocess.run(...).stdout without `check=True` and without reading\n"
        "`returncode` first turns a failure into empty output. Bind the result\n"
        "and check `returncode` before using stdout.",
        where=is_python,
    ),
    Rule(
        "lstrip-v-multiple",
        r"\.lstrip\s*\(\s*['\"]v['\"]\s*\)",
        "`.lstrip('v')` strips ALL leading v's. Use `s[1:] if s[:1]=='v' else s`\n"
        "or `s.removeprefix('v')`.",
        where=is_python,
    ),
    Rule(
        "pubspec-version-greedy",
        r'r?["\']\^\(version:\\s\*\)\.\*\$["\']',
        "Dart pubspec uses X.Y.Z+<build>. The greedy `.*$` strips `+build`.\n"
        "Anchor to `\\d+\\.\\d+\\.\\d+(\\+[^\\s#]+)?` and preserve the build.",
        where=is_python,
    ),
    Rule(
        "set-plus-e",
        r"^\s*set\s+\+e\b",
        "`set +e` disables errexit — hides failures. Remove it or scope to a\n"
        "single `|| true` on the one line that is legitimately allowed to fail.",
        where=is_shell,
    ),
]


def iter_files(root: pathlib.Path):
    skip_dirs = {".git", "node_modules", ".venv", "__pycache__"}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(root).parts
        if any(part in skip_dirs for part in parts):
            continue
        yield p


def main() -> int:
    hits: list[tuple[Rule, pathlib.Path, int, str]] = []
    for path in iter_files(ROOT):
        applicable = [r for r in RULES if r.where(path)]
        if not applicable:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(ROOT)
        for rule in applicable:
            for m in rule.pattern.finditer(text):
                if rule.extra_check and not rule.extra_check(text, m):
                    continue
                lineno = text.count("\n", 0, m.start()) + 1
                line = text.splitlines()[lineno - 1].rstrip()
                hits.append((rule, rel, lineno, line))

    if not hits:
        print(f"ok: {sum(1 for _ in iter_files(ROOT))} files scanned, no "
              f"bad-pattern hits across {len(RULES)} rules")
        return 0

    by_rule: dict[str, list[tuple[pathlib.Path, int, str]]] = {}
    for rule, path, lineno, line in hits:
        by_rule.setdefault(rule.name, []).append((path, lineno, line))

    for rule_name, matches in by_rule.items():
        rule = next(r for r in RULES if r.name == rule_name)
        print(f"\n## {rule_name} ({len(matches)} hit{'s' if len(matches)!=1 else ''})")
        for p, ln, line in matches:
            print(f"::error file={p},line={ln},title=audit[{rule_name}]::{line.strip()[:160]}")
            print(f"  {p}:{ln}  {line[:140]}")
        for note_line in rule.note.splitlines():
            print(f"  | {note_line}")

    print(f"\nFAIL: {len(hits)} bad-pattern hit(s) across {len(by_rule)} rule(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
