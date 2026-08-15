---
name: jqcli
description: "Use when Codex needs to operate or maintain the jqcli JoinQuant project: authenticate, inspect strategies or research files, discover research kernels and sessions, run explicitly approved temporary research code or notebooks, list or run backtests, archive community posts, validate jqcli API behavior, run local tests, perform live JoinQuant smoke checks, or troubleshoot jqcli CLI/API workflows."
---

# jqcli

Use this skill to work with the jqcli project and its JoinQuant workflows.

## Locate The Project

Default local path:

```text
D:\project\jqcli
```

If the workspace differs, locate the repo by finding `pyproject.toml` with `name = "jqcli"`.

## Command Entry Points

Prefer the installed console script:

```powershell
.\.venv\Scripts\jqcli.exe --format json auth status
```

Do not use `python -m jqcli.cli` for CLI operation; direct module execution can hit circular imports. Use the virtualenv Python only for tests and scripts:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

If dependencies are missing:

```powershell
uv sync --extra test
```

## Local Data Boundary

Treat `local/` as private workspace state. It contains local data, experiments, logs, marketing assets, and local-only scripts. It is ignored by git.

Do not move `local/` contents back to the repo root. When adding new generated outputs, default them under `local/data/`, `local/experiments/`, `local/logs/`, or `local/scripts/`.

## Common Workflows

Read `references/api-workflows.md` when the user asks to run jqcli commands, test APIs, validate live JoinQuant compatibility, or perform read/write smoke checks.

Read `references/troubleshooting.md` when a command fails, authentication behaves oddly, tests fail, or live API responses look like login redirects or "system busy" responses.

Use helper scripts when available:

```powershell
.\codex-skill\jqcli\scripts\resolve_jqcli.ps1
.\codex-skill\jqcli\scripts\smoke_readonly.ps1
.\codex-skill\jqcli\scripts\smoke_write_compile.ps1
```

## Safety Rules

Prefer read-only live checks before write checks. Run write smoke checks only when the user asks for full live validation or explicitly approves live mutations.

For write smoke checks, create a temporary strategy, run compile-only backtest, read result/logs, delete the compile record, and delete the temporary strategy.

Never run `strategy rm`, `backtest rm`, `strategy edit`, or `backtest run` against user assets unless the command targets a temporary smoke-test object or the user explicitly identifies the target.

Research smoke checks are read-only. Prefer `research ls`, metadata-only `research show`, and `research kernelspecs/kernels/sessions`; report only counts and booleans, never research paths, file names, contents, kernel/session identifiers, or kernel-spec names.

Never run `research upload`, `research mkdir`, `research mv`, or `research rm` against user assets during a smoke check. Use those commands only when the user explicitly identifies the target or approves a temporary research object and its cleanup.

Never include `research exec` or `research run` in the standard read-only smoke check. Remote code can have arbitrary side effects, so run it only when the user explicitly approves execution, always pass `--yes`, and use the minimum code or explicitly named Notebook required by the task. Both commands create an exclusive high-entropy temporary session and new kernel; the `exec` session does not save a research file. jqcli cleans them up, never reuses existing objects, and does not save `research run` outputs back to the Notebook.

## Verification

Before claiming jqcli is working, run the relevant fresh command and read its exit code and output.

For local correctness:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

For API-layer correctness:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_api -q
```
