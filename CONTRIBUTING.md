# Contributing

This repository is unusually strict about evidence: a change is only "done"
when there is a real command and a real output behind it. Read the two
authority files before your first commit — they are short and they will save
you a rejected PR.

## Read first (in this order)

| What you want to know | Read |
|-----------------------|------|
| When am I allowed to say "done"? | [`policy.md`](policy.md) — delivery gates, task grading, evidence labels |
| Which test proves which link in the chain? | [`test_ledger.md`](test_ledger.md) — seam registry S01–S34 |
| What are the non-negotiable rules? | [`CLAUDE.md`](CLAUDE.md) §3 (conventions) and §5 (red lines, each with a guard test) |
| How do I add a feature / API? | [`skills/gcm-feature/SKILL.md`](skills/gcm-feature/SKILL.md) |
| What is already tested (so you don't re-run it)? | [`.agent/memory/tested-scope.md`](.agent/memory/tested-scope.md) |
| Why was it designed this way? | [`docs/architecture.md`](docs/architecture.md) + [`docs/adr/`](docs/adr/) |

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# GUI
python -m gcm

# Headless
QT_QPA_PLATFORM=offscreen python -m gcm --cli --json urls.txt
```

Requires **Python 3.10+** and a system `git` on `PATH`.

## The quality gate (CI runs exactly this)

```bash
python -m ruff check gcm        # must be 0 errors (CI lints gcm only)
python -m mypy gcm              # must succeed

QT_QPA_PLATFORM=offscreen python -m unittest tests.test_<your_new_test> -v
./.venv/Scripts/python.exe -m coverage run --source=gcm scripts/ci_run_fast.py
```

Run the **narrowest** test that proves your change, then widen only when the
change is cross-module, architectural, or lands as a release candidate.

## Rules that will bite you

1. **Version has one source** — `gcm/__init__.py`'s `__version__`. Any other
   literal version (pyproject, installer.iss, spec, plugin.json, docs) is a bug
   and a guard test will fail.
2. **Never weaken an assertion to make a test green.** Do not skip, do not
   retry-to-green, do not mock the thing under test.
3. **New module or function needs a production call site.** Landing only in
   `tests/` is a rejected change (`TestG69_18WiringClosure`).
4. **`subprocess.run(text=True)` must pass an explicit `encoding`** — `utf-8`
   for git, `_console_encoding()` for Windows console tools like `schtasks`.
5. **Never swallow exceptions silently.** `except Exception` must log or give
   an actionable message; the bare-`pass` budget is frozen in a guard test.
6. **Redact before it leaves the process** — UI, logs, CSV/XLSX, MCP, CLI.
7. **New user-visible text goes through `tr()`**, with keys added to both
   `gcm/i18n/zh.py` and `gcm/i18n/en.py`.
8. **Signal connections bind methods, not lambdas capturing `self`** —
   lambdas recreate a reference cycle that historically caused native crashes.

## Adding a test

New test files land in the **fast CI set by default** (`scripts/ci_run_fast.py`
uses a blacklist, not a whitelist). Name the file `tests/test_<area>.py` and
register the new seam in `test_ledger.md` if two modules start talking through
a new contract.

## Commits

One batch per commit. Do not mix unrelated refactors into a functional change.
Describe *why*, and name the command you ran to verify it.

## License

By contributing you agree your work is licensed under the [MIT License](LICENSE).
