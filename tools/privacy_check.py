#!/usr/bin/env python3
"""Release check for the publishable tree.

Scans content that is about to be published for material that must stay private:

* ``--staged``   the Git index (what a commit would contain), not the working tree
* ``--files``    explicit files in the working tree
* ``--history``  every commit reachable in the repository: tree contents, commit
                 messages and author/committer metadata

Two kinds of rules apply.  Generic rules (keys, private-key blocks, home-directory
paths, e-mail addresses, attribution trailers) are built in.  Project-specific terms
live in a *policy file that is kept outside this repository* and is located through
``--policy``, the ``RELEASE_POLICY_FILE`` environment variable or the repo-local Git
setting ``release.policyfile``.  Without a policy file the check fails closed.

Every published path must also match ``tools/release_allowlist.txt``; symlinks, binary
files, unexpected file types and files over 2 MB are rejected.

Findings print a rule id and a location only.  Matched text, policy terms and
credentials are never echoed.  Exit status: 0 clean, 1 findings, 2 configuration error.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 2_000_000
TEXT_EXTENSIONS = {".py", ".md", ".txt", ".sh", ".json", ".toml", ".cfg", ".ini", ".yml", ".yaml", ""}
GENERIC_RULES = [
    ("G01-private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("G02-cloud-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("G03-api-token", re.compile(
        r"\b(?:(?:sk|pk|rk)[-_](?:live|test|proj|ant)?[-_]?[A-Za-z0-9_\-]{16,}"
        r"|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
        r"|glpat-[A-Za-z0-9_\-]{20,}|xox[abprs]-[A-Za-z0-9\-]{10,}|AIza[0-9A-Za-z_\-]{30,})\b")),
    ("G04-secret-assignment", re.compile(r"(?i)\b(?:api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+]{12,}")),
    # character classes keep these patterns from matching their own source text
    ("G05-home-directory-path", re.compile(r"(?:/U[s]ers/|/h[o]me/|C:\\\\U[s]ers\\\\)[^/\\\s'\"`]+")),
    ("G06-email-address", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("G07-attribution-trailer", re.compile(r"(?im)^\s*co[-\s]authored[-\s]by\s*:")),
]


class ConfigError(Exception):
    pass


def git(repo: Path, *args: str, binary: bool = False):
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if res.returncode != 0:
        raise ConfigError(f"git {' '.join(args[:2])} failed")
    return res.stdout if binary else res.stdout.decode("utf-8", "replace")


def resolve_policy(repo: Path, explicit: str | None) -> Path:
    candidates = [explicit, os.environ.get("RELEASE_POLICY_FILE")]
    try:
        candidates.append(git(repo, "config", "--get", "release.policyfile").strip())
    except ConfigError:
        pass
    for c in candidates:
        if c and Path(c).expanduser().is_file():
            return Path(c).expanduser()
    raise ConfigError("no release policy file configured (use --policy, RELEASE_POLICY_FILE or git config release.policyfile)")


def load_policy(path: Path):
    terms, regexes = [], []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            regexes.append(re.compile(line[3:].strip(), re.I))
        else:
            terms.append(line.lower())
    if not terms and not regexes:
        raise ConfigError("release policy file is empty")
    return terms, regexes


def load_allowlist(path: Path) -> list[str]:
    if not path.is_file():
        raise ConfigError("release allowlist is missing")
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def allowed(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pat) for pat in patterns)


def scan_text(text: str, terms, regexes, where: str, findings: list, generic=GENERIC_RULES) -> None:
    low = text.lower()

    def line_of(pos: int) -> int:
        return text.count("\n", 0, pos) + 1

    for i, term in enumerate(terms):
        pos = low.find(term)
        if pos >= 0:
            findings.append((f"P-term-{i + 1:02d}", f"{where}:{line_of(pos)}"))
    for i, rx in enumerate(regexes):
        m = rx.search(text)
        if m:
            findings.append((f"P-re-{i + 1:02d}", f"{where}:{line_of(m.start())}"))
    for rule, rx in generic:
        m = rx.search(text)
        if m:
            findings.append((rule, f"{where}:{line_of(m.start())}"))


def check_blob(path: str, data: bytes, mode: str | None, patterns, terms, regexes, findings: list, where: str) -> None:
    if not allowed(path, patterns):
        findings.append(("A01-path-not-in-allowlist", where))
    scan_text(path, terms, regexes, f"path:{where}", findings, generic=[r for r in GENERIC_RULES if r[0].startswith("G05")])
    if mode == "120000":
        findings.append(("F01-symlink", where))
        return
    if len(data) > MAX_BYTES:
        findings.append(("F02-file-too-large", where))
    if b"\x00" in data:
        findings.append(("F03-binary-content", where))
        return
    ext = Path(path).suffix.lower()
    if ext not in TEXT_EXTENSIONS:
        findings.append(("F04-unexpected-file-type", where))
    scan_text(data.decode("utf-8", "replace"), terms, regexes, where, findings)


def check_staged(repo: Path, patterns, terms, regexes, findings: list) -> int:
    listing = git(repo, "ls-files", "--stage", "-z")
    modes = {}
    for entry in listing.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        modes[path] = meta.split()[0]
    changed = [p for p in git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0") if p]
    for path in changed:
        data = git(repo, "show", f":{path}", binary=True)
        check_blob(path, data, modes.get(path), patterns, terms, regexes, findings, f"index:{path}")
    return len(changed)


def check_files(repo: Path, files: list[str], patterns, terms, regexes, findings: list) -> int:
    for f in files:
        p = Path(f)
        rel = os.path.relpath(p.resolve(), repo.resolve()) if p.is_absolute() or p.exists() else f
        if p.is_symlink():
            findings.append(("F01-symlink", rel))
            continue
        if not p.is_file():
            findings.append(("F05-missing-file", rel))
            continue
        check_blob(rel, p.read_bytes(), None, patterns, terms, regexes, findings, rel)
    return len(files)


def check_history(repo: Path, patterns, terms, regexes, findings: list) -> int:
    try:
        commits = [c for c in git(repo, "rev-list", "--all").split() if c]
    except ConfigError:
        return 0
    for sha in commits:
        # Author/committer name and e-mail fields are e-mail addresses by nature: they get the
        # policy terms and the attribution-trailer rule only.  The commit message body gets
        # every rule, exactly like file content.
        ident = git(repo, "log", "-1", "--format=%an%n%ae%n%cn%n%ce", sha)
        scan_text(ident, terms, regexes, f"commit:{sha[:12]}:author", findings,
                  generic=[r for r in GENERIC_RULES if r[0].startswith("G07")])
        body = git(repo, "log", "-1", "--format=%B", sha)
        scan_text(body, terms, regexes, f"commit:{sha[:12]}:message", findings)
        tree = git(repo, "ls-tree", "-r", "-z", sha)
        for entry in tree.split("\0"):
            if not entry:
                continue
            meta_part, path = entry.split("\t", 1)
            mode = meta_part.split()[0]
            data = git(repo, "show", f"{sha}:{path}", binary=True)
            check_blob(path, data, mode, patterns, terms, regexes, findings, f"commit:{sha[:12]}:{path}")
    return len(commits)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=".", help="repository root (default: current directory)")
    ap.add_argument("--policy", help="private policy file (never committed)")
    ap.add_argument("--allowlist", help="allowlist file (default: tools/release_allowlist.txt in the repo)")
    ap.add_argument("--staged", action="store_true", help="scan the Git index")
    ap.add_argument("--history", action="store_true", help="scan all commits: trees, messages and metadata")
    ap.add_argument("--files", nargs="*", help="scan these working-tree files")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    if not (args.staged or args.history or args.files):
        ap.error("choose at least one of --staged, --history, --files")
    try:
        policy = resolve_policy(repo, args.policy)
        terms, regexes = load_policy(policy)
        patterns = load_allowlist(Path(args.allowlist) if args.allowlist else repo / "tools" / "release_allowlist.txt")
        findings: list = []
        scanned = 0
        if args.staged:
            scanned += check_staged(repo, patterns, terms, regexes, findings)
        if args.files:
            scanned += check_files(repo, args.files, patterns, terms, regexes, findings)
        if args.history:
            scanned += check_history(repo, patterns, terms, regexes, findings)
    except ConfigError as e:
        print(f"release-check: configuration error: {e}", file=sys.stderr)
        return 2
    seen = set()
    for rule, where in findings:
        if (rule, where) in seen:
            continue
        seen.add((rule, where))
        print(f"FAIL {rule} {where}")
    if seen:
        print(f"release-check: {len(seen)} finding(s) in {scanned} item(s); nothing may be published", file=sys.stderr)
        return 1
    print(f"release-check: clean ({scanned} item(s) scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
