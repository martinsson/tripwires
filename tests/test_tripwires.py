"""Each tripwire: green at the limit, red one over, and the run reports everything."""

from pathlib import Path

import pytest

import tripwires
from tripwires import Crossing, check


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _function_of(lines: int, name: str = "f", params: str = "") -> str:
    return f"def {name}({params}):\n" + "    x = 1\n" * (lines - 1)


def _repo_with_tests(root: Path) -> None:
    """Enough test lines that the ratio tripwire stays quiet in tests about other tripwires."""
    for i in range(5):
        _write(root, f"tests/test_x{i}.py", "x = 1\n" * 300)


def test_given_a_file_at_the_limit_when_checked_then_green(tmp_path):
    _repo_with_tests(tmp_path)
    _write(tmp_path, "src/a.py", "x = 1\n" * tripwires.FILE_LINES)
    assert check(tmp_path) == []


def test_given_a_file_one_over_the_limit_when_checked_then_red_with_file_and_value(tmp_path):
    _repo_with_tests(tmp_path)
    _write(tmp_path, "src/a.py", "x = 1\n" * (tripwires.FILE_LINES + 1))
    assert check(tmp_path) == [Crossing("file-length", "src/a.py", tripwires.FILE_LINES + 1, tripwires.FILE_LINES)]


def test_given_a_function_one_over_the_limit_when_checked_then_red(tmp_path):
    _repo_with_tests(tmp_path)
    _write(tmp_path, "src/a.py", _function_of(tripwires.FUNCTION_LINES + 1))
    expected = Crossing("function-length", "src/a.py:f", tripwires.FUNCTION_LINES + 1, tripwires.FUNCTION_LINES)
    assert check(tmp_path) == [expected]


def test_given_a_tenth_parameter_when_checked_then_red_and_self_is_not_counted(tmp_path):
    _repo_with_tests(tmp_path)
    ten = ", ".join(f"p{i}" for i in range(10))
    nine = ", ".join(f"p{i}" for i in range(9))
    _write(tmp_path, "src/a.py", f"class C:\n    def __init__(self, {ten}): pass\n    def ok(self, {nine}): pass\n")
    assert check(tmp_path) == [Crossing("parameters", "src/a.py:__init__", 10, tripwires.PARAMETERS)]


def test_given_a_class_one_over_the_limit_when_checked_then_red(tmp_path):
    _repo_with_tests(tmp_path)
    _write(tmp_path, "src/a.py", "class C:\n" + "    x = 1\n" * tripwires.CLASS_LINES)
    expected = Crossing("class-length", "src/a.py:C", tripwires.CLASS_LINES + 1, tripwires.CLASS_LINES)
    assert check(tmp_path) == [expected]


def test_given_nesting_one_over_the_limit_when_checked_then_red(tmp_path):
    _repo_with_tests(tmp_path)
    depth = tripwires.NESTING_DEPTH + 1
    body = "".join("    " * (i + 1) + "if x:\n" for i in range(depth)) + "    " * (depth + 1) + "pass\n"
    _write(tmp_path, "src/a.py", "def f(x):\n" + body)
    assert check(tmp_path) == [Crossing("nesting-depth", "src/a.py:f", depth, tripwires.NESTING_DEPTH)]


def test_given_two_crossings_in_different_files_when_checked_then_both_are_reported(tmp_path):
    _repo_with_tests(tmp_path)
    _write(tmp_path, "src/a.py", _function_of(tripwires.FUNCTION_LINES + 1))
    _write(tmp_path, "src/b.py", _function_of(tripwires.FUNCTION_LINES + 5, name="g"))
    assert [(c.tripwire, c.where) for c in check(tmp_path)] == [
        ("function-length", "src/a.py:f"),
        ("function-length", "src/b.py:g"),
    ]


def test_given_a_file_that_does_not_parse_when_checked_then_red_not_skipped(tmp_path):
    _repo_with_tests(tmp_path)
    _write(tmp_path, "src/a.py", "def (:\n")
    with pytest.raises(SystemExit, match="src/a.py does not parse"):
        check(tmp_path)


def test_given_a_flat_tests_dir_with_40_files_when_checked_then_green(tmp_path):
    for i in range(40):
        _write(tmp_path, f"tests/test_{i}.py", "x = 1\n" * 30)
    _write(tmp_path, "src/a.py", "x = 1\n")
    assert check(tmp_path) == []


def test_given_a_source_dir_one_over_the_limit_when_checked_then_red(tmp_path):
    _repo_with_tests(tmp_path)
    for i in range(tripwires.FILES_PER_DIR + 1):
        _write(tmp_path, f"src/web/m{i}.py", "x = 1\n")
    expected = Crossing("files-per-dir", "src/web", tripwires.FILES_PER_DIR + 1, tripwires.FILES_PER_DIR)
    assert check(tmp_path) == [expected]


def test_given_suppressions_one_over_the_limit_when_checked_then_red(tmp_path):
    _repo_with_tests(tmp_path)
    _write(tmp_path, "src/a.py", "x = 1  # noqa\n" * (tripwires.SUPPRESSIONS + 1))
    expected = Crossing("suppressions", "(repo)", tripwires.SUPPRESSIONS + 1, tripwires.SUPPRESSIONS)
    assert check(tmp_path) == [expected]


def test_given_skipped_tests_one_over_the_limit_when_checked_then_red(tmp_path):
    _repo_with_tests(tmp_path)
    _write(tmp_path, "tests/test_a.py", "@pytest.mark.skip\ndef t(): pass\n" * (tripwires.SKIPPED_TESTS + 1))
    _write(tmp_path, "src/a.py", "x = 1\n")
    expected = Crossing("skipped-tests", "(repo)", tripwires.SKIPPED_TESTS + 1, tripwires.SKIPPED_TESTS)
    assert check(tmp_path) == [expected]


def test_given_swallowed_exceptions_one_over_the_limit_when_checked_then_red(tmp_path):
    _repo_with_tests(tmp_path)
    handlers = "try:\n    pass\nexcept ValueError:\n    pass\n" * tripwires.SWALLOWED_EXCEPTIONS
    bare = "try:\n    pass\nexcept:\n    raise\n"
    _write(tmp_path, "src/a.py", handlers + bare)
    n = tripwires.SWALLOWED_EXCEPTIONS + 1
    assert check(tmp_path) == [Crossing("swallowed-exceptions", "(repo)", n, tripwires.SWALLOWED_EXCEPTIONS)]


def test_given_baseline_rows_one_over_the_limit_when_checked_then_red_ignoring_comments(tmp_path):
    _repo_with_tests(tmp_path)
    _write(tmp_path, "src/a.py", "x = 1\n")
    rows = "# header\n\n" + "rule\tpath\t1\n" * (tripwires.BASELINE_ROWS + 1)
    _write(tmp_path, "config/lint_design_baseline.txt", rows)
    n = tripwires.BASELINE_ROWS + 1
    expected = Crossing("baseline-rows", "config/lint_design_baseline.txt", n, tripwires.BASELINE_ROWS)
    assert check(tmp_path) == [expected]


def test_given_claude_md_one_over_the_limit_when_checked_then_red(tmp_path):
    _repo_with_tests(tmp_path)
    _write(tmp_path, "src/a.py", "x = 1\n")
    _write(tmp_path, "CLAUDE.md", "- note\n" * (tripwires.CLAUDE_MD_LINES + 1))
    n = tripwires.CLAUDE_MD_LINES + 1
    assert check(tmp_path) == [Crossing("claude-md-length", "CLAUDE.md", n, tripwires.CLAUDE_MD_LINES)]


def test_given_test_loc_below_half_of_src_loc_when_checked_then_red(tmp_path):
    _write(tmp_path, "src/a.py", "x = 1\n" * 100)
    _write(tmp_path, "tests/test_a.py", "x = 1\n" * 49)
    assert check(tmp_path) == [Crossing("test-to-src-ratio", "(repo) 49/100", 0.49, tripwires.TEST_TO_SRC_RATIO)]


def test_given_vendored_dirs_when_checked_then_they_are_not_measured(tmp_path):
    _repo_with_tests(tmp_path)
    _write(tmp_path, "src/a.py", "x = 1\n")
    _write(tmp_path, ".venv/lib/huge.py", "x = 1\n" * (tripwires.FILE_LINES + 1))
    _write(tmp_path, "node_modules/x/huge.py", "x = 1\n" * (tripwires.FILE_LINES + 1))
    assert check(tmp_path) == []


def test_given_crossings_when_reported_then_every_one_is_listed_with_its_value():
    crossings = [Crossing("file-length", "src/a.py", 701, 700), Crossing("parameters", "src/b.py:f", 10, 9)]
    assert tripwires.report(crossings) == (
        "tripwires: 2 crossed\n"
        "  file-length            src/a.py  701 > 700\n"
        "  parameters             src/b.py:f  10 > 9"
    )
