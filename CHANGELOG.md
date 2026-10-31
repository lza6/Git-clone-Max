# Changelog

> This file is a **pointer, not a copy**.
>
> The authoritative, per-version changelog lives in the source of truth:
> **`gcm/__init__.py` → `__changelog__`** (one entry per released version,
> newest first), and the released artifacts on the
> [Releases page](https://github.com/lza6/Git-clone-Max/releases).
>
> Keeping the text in exactly one place is deliberate. A duplicated changelog
> drifts from the code within two releases and then nobody can tell which one
> is true.

## Why there is no list here

The project treats the version string as a single-source contract
(`gcm/__init__.py` → `__version__`), enforced by guard tests that fail if any
other file carries a stale version literal. Duplicating release notes here
would create a second, unverified source for the same facts.

## How to read the history

```bash
# Every version, newest first
python -c "import gcm; print('\n'.join(gcm.__changelog__))"

# Current version only
python -c "import gcm; print(gcm.__version__)"
```

In the app: **Help → About**, or **设置页 → 版本信息**.

## How release notes are produced

Each release adds exactly one entry to `__changelog__` covering: what changed,
which user-visible behaviour it affects, and how it was verified (test names,
command output, coverage). See [`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md).

Verification evidence for each round is kept under
[`docs/验证记录-*.md`](docs/) and [`evidence/`](evidence/).
