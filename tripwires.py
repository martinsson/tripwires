#!/usr/bin/env python3
"""Tripwires: absolute structural limits a healthy codebase never reaches.

This is not a linter. The thresholds sit far beyond anything the design gates
in the measured repository allow, so a crossing means something slipped past
every review and every ratchet — and nobody was looking. There is no baseline
and no per-file exception on purpose: a baseline is the first thing that gets
appended to. The one answer to a red run is to fix the offender.

It runs only on the default branch, after a merge and on a schedule, never on a
pull request: a check nobody has to turn green is a check nobody learns to
route around.

Usage:  python3 tripwires.py <repo-root> [--email-to ADDR]
        exit 1 if any tripwire is crossed; a file that does not parse is red,
        never skipped.

Dependency-free. Python sources only. E-mail needs EMAIL_USER / EMAIL_PASSWORD
(EMAIL_SMTP_HOST / EMAIL_SMTP_PORT optional, Gmail by default).
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import smtplib
import sys
from collections import Counter
from dataclasses import dataclass
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterator

SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", ".claude", "__pycache__", "site-packages", "dist", "build", ".jscpd"}
)

# The limits. Absolute, global, deliberately extreme. A value strictly above
# the limit is a crossing.
FILE_LINES = 700
FUNCTION_LINES = 100
CLASS_LINES = 500
PARAMETERS = 9  # a tenth parameter is the alarm; self/cls are not counted
NESTING_DEPTH = 6
FILES_PER_DIR = 10  # tests/ excluded: one flat directory of tests is the convention
SUPPRESSIONS = 30
SKIPPED_TESTS = 5
SWALLOWED_EXCEPTIONS = 5
BASELINE_ROWS = 20
CLAUDE_MD_LINES = 500
TEST_TO_SRC_RATIO = 0.5

_SUPPRESSION_RE = re.compile(r"#\s*(noqa|type:\s*ignore|pragma:\s*no cover)")
_SKIP_RE = re.compile(r"pytest\.mark\.(skip|xfail)|pytest\.(skip|xfail)\(")
_COMMENT_RE = re.compile(r"^\s*(#|$)")
_BLOCK_STMTS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try, ast.Match)


@dataclass(frozen=True)
class Crossing:
    tripwire: str
    where: str
    value: float
    limit: float

    def __str__(self) -> str:
        return f"  {self.tripwire:<22} {self.where}  {self.value:g} > {self.limit:g}"


@dataclass(frozen=True)
class Source:
    path: Path
    rel: str
    text: str

    @property
    def is_test(self) -> bool:
        parts = Path(self.rel).parts
        return "tests" in parts or "test" in parts or Path(self.rel).name.startswith("test_")

    @property
    def line_count(self) -> int:
        return self.text.count("\n") + (1 if self.text and not self.text.endswith("\n") else 0)

    def tree(self) -> ast.AST:
        try:
            return ast.parse(self.text)
        except (SyntaxError, ValueError) as exc:
            raise SystemExit(f"tripwires: {self.rel} does not parse ({exc}) — red, not skipped")


def _sources(root: Path) -> list[Source]:
    found = []
    for path in sorted(root.rglob("*.py")):
        if SKIP_DIRS.intersection(path.relative_to(root).parts):
            continue
        found.append(Source(path, str(path.relative_to(root)), path.read_text(encoding="utf-8")))
    return found


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    a = node.args
    names = [p.arg for p in a.posonlyargs + a.args + a.kwonlyargs]
    return len([n for n in names if n not in ("self", "cls")])


def _depth(node: ast.AST, current: int = 0) -> int:
    deepest = current
    for child in ast.iter_child_nodes(node):
        inner = current + 1 if isinstance(child, _BLOCK_STMTS) else current
        deepest = max(deepest, _depth(child, inner))
    return deepest


def _swallows(handler: ast.ExceptHandler) -> bool:
    body_is_nothing = all(
        isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is ...)
        for s in handler.body
    )
    return handler.type is None or body_is_nothing


def _per_file(src: Source) -> Iterator[Crossing]:
    if src.line_count > FILE_LINES:
        yield Crossing("file-length", src.rel, src.line_count, FILE_LINES)
    for node in ast.walk(src.tree()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            where = f"{src.rel}:{node.name}"
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            if length > FUNCTION_LINES:
                yield Crossing("function-length", where, length, FUNCTION_LINES)
            if _parameters(node) > PARAMETERS:
                yield Crossing("parameters", where, _parameters(node), PARAMETERS)
            if _depth(node) > NESTING_DEPTH:
                yield Crossing("nesting-depth", where, _depth(node), NESTING_DEPTH)
        elif isinstance(node, ast.ClassDef):
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            if length > CLASS_LINES:
                yield Crossing("class-length", f"{src.rel}:{node.name}", length, CLASS_LINES)


def _repo_wide(root: Path, sources: list[Source]) -> Iterator[Crossing]:
    per_dir = Counter(str(Path(s.rel).parent) for s in sources if not s.is_test)
    for directory, n in sorted(per_dir.items()):
        if n > FILES_PER_DIR:
            yield Crossing("files-per-dir", directory, n, FILES_PER_DIR)

    suppressions = sum(len(_SUPPRESSION_RE.findall(s.text)) for s in sources)
    if suppressions > SUPPRESSIONS:
        yield Crossing("suppressions", "(repo)", suppressions, SUPPRESSIONS)

    skipped = sum(len(_SKIP_RE.findall(s.text)) for s in sources if s.is_test)
    if skipped > SKIPPED_TESTS:
        yield Crossing("skipped-tests", "(repo)", skipped, SKIPPED_TESTS)

    swallowed = sum(
        1 for s in sources for n in ast.walk(s.tree()) if isinstance(n, ast.ExceptHandler) and _swallows(n)
    )
    if swallowed > SWALLOWED_EXCEPTIONS:
        yield Crossing("swallowed-exceptions", "(repo)", swallowed, SWALLOWED_EXCEPTIONS)

    baseline = root / "config" / "lint_design_baseline.txt"
    if baseline.exists():
        rows = sum(1 for line in baseline.read_text().splitlines() if not _COMMENT_RE.match(line))
        if rows > BASELINE_ROWS:
            yield Crossing("baseline-rows", str(baseline.relative_to(root)), rows, BASELINE_ROWS)

    claude_md = root / "CLAUDE.md"
    if claude_md.exists():
        lines = len(claude_md.read_text().splitlines())
        if lines > CLAUDE_MD_LINES:
            yield Crossing("claude-md-length", "CLAUDE.md", lines, CLAUDE_MD_LINES)

    src_loc = sum(s.line_count for s in sources if not s.is_test)
    test_loc = sum(s.line_count for s in sources if s.is_test)
    if src_loc and test_loc / src_loc < TEST_TO_SRC_RATIO:
        yield Crossing("test-to-src-ratio", f"(repo) {test_loc}/{src_loc}", round(test_loc / src_loc, 2), TEST_TO_SRC_RATIO)


def check(root: Path) -> list[Crossing]:
    """Every crossing in the tree, or an empty list. Raises SystemExit on an unparseable file."""
    sources = _sources(root)
    crossings = [c for s in sources for c in _per_file(s)]
    crossings += list(_repo_wide(root, sources))
    return crossings


def report(crossings: list[Crossing]) -> str:
    if not crossings:
        return "tripwires: none crossed"
    lines = [f"tripwires: {len(crossings)} crossed"] + [str(c) for c in crossings]
    return "\n".join(lines)


def _env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise SystemExit(f"tripwires: {key} is not set")
    return value


def send_email(to_addr: str, subject: str, body: str) -> None:
    user, password = _env("EMAIL_USER"), _env("EMAIL_PASSWORD")
    host = os.environ.get("EMAIL_SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("EMAIL_SMTP_PORT") or 587)
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"], msg["To"], msg["Subject"] = user, to_addr, subject
    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.sendmail(user, to_addr, msg.as_string())


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Absolute structural limits a healthy codebase never reaches.")
    ap.add_argument("root", type=Path)
    ap.add_argument("--email-to", default=None, help="mail the report when something is crossed")
    ap.add_argument("--repo-name", default=None, help="for the mail subject")
    a = ap.parse_args(argv)

    crossings = check(a.root.resolve())
    text = report(crossings)
    print(text)
    if crossings and a.email_to:
        name = a.repo_name or a.root.resolve().name
        send_email(a.email_to, f"[tripwire] {name}: {len(crossings)} crossed", text)
        print(f"report mailed to {a.email_to}")
    return 1 if crossings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
