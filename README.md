# tripwires

Absolute structural limits a healthy codebase never reaches. When one is
crossed, something slipped past every review, gate and ratchet in the measured
repository, and nobody was looking. This repo exists so that a human hears
about it anyway.

It is **not** a linter, and it is deliberately kept out of the repositories it
measures:

- **No baseline, no exceptions, no config in the measured repo.** A baseline is
  the first thing that gets appended to. The one answer to a red run is to fix
  the offender (or, rarely, to change the limit here, on purpose).
- **Runs on the default branch after a merge and on a schedule. Never on a pull
  request.** A check nobody has to turn green is a check nobody learns to route
  around. Keep it out of the caller's Makefile, CLAUDE.md and skills for the
  same reason.
- **A missing or unparseable file is red, never skipped.**

## The wires

| Tripwire | Limit (strictly above is red) |
|---|---|
| `file-length` | 700 lines |
| `function-length` | 100 lines |
| `class-length` | 500 lines |
| `parameters` | 9 (a tenth parameter; `self`/`cls` not counted) |
| `nesting-depth` | 6 nested blocks |
| `files-per-dir` | 10 `.py` files, `tests/` excluded |
| `suppressions` | 30 `noqa` / `type: ignore` / `pragma: no cover` in the repo |
| `skipped-tests` | 5 `skip` / `xfail` |
| `swallowed-exceptions` | 5 bare `except:` or `except X: pass` |
| `baseline-rows` | 20 rows in `config/lint_design_baseline.txt` |
| `claude-md-length` | 500 lines |
| `test-to-src-ratio` | test LOC below half of source LOC |

Python sources only. `.git`, virtualenvs, `node_modules`, `.claude`, build
output are not measured.

## Use from another repository

```yaml
# .github/workflows/tripwires.yml in the measured repo
name: Tripwires
on:
  push:
    branches: [main]
  schedule:
    - cron: "0 6 * * 1"
jobs:
  tripwires:
    uses: martinsson/tripwires/.github/workflows/check.yml@main
    secrets: inherit
```

This repo is private, so it must allow access from the owner's other
repositories: *Settings → Actions → General → Access → "Accessible from
repositories owned by the user"*. That setting covers both the reusable
workflow and the composite action (`action.yml`) it runs, so the caller needs
no token of its own.

Mail: `EMAIL_USER` / `EMAIL_PASSWORD` in the caller's secrets, optional
`ALERT_EMAIL_TO` (default martinsson.rouveyrol@gmail.com), `EMAIL_SMTP_HOST`,
`EMAIL_SMTP_PORT`. Without them the failed run itself is the alert.

## By hand

```
python3 tripwires.py /path/to/repo
python3 -m pytest -q
```
