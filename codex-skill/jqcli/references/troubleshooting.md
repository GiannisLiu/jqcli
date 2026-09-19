# jqcli Troubleshooting

## Simulation Data Dates and Incomplete Snapshots

- An empty `simulation returns <id> --today` on a non-trading day is valid. Daily-frequency simulations can also lack intraday curves. Keep the date and empty result rather than reporting zero return or the last trading day's return as today's.
- `positions` may return prior trading-day records. Use `record_dates` and row `time` to label the actual holdings date.
- `complete=false` in a record query means `isLimit=true` for at least one day. Query that date using `positions/orders --start <date> --end <date>` and inspect the flag. `--limit` defaults to 10000; raising it may help only if the server honors it. Narrowing a multi-day range does not remove a per-day cap. Offset pagination has not been verified for these endpoints.
- `simulation sync` does not publish partial results. If it fails, the prior output is retained and is not fresh. Check the error, fix authentication or the failing data query, then rerun with the intended scope.
- Refresh IDs from `simulation ls` when detail resolution fails. Simulation commands require login; `auth status` alone does not remotely verify a saved cookie.

## CLI Import Error

Symptom:

```text
ImportError: cannot import name 'auth_group' from partially initialized module
```

Cause: the CLI was invoked with `python -m jqcli.cli`.

Fix: use the console script:

```powershell
.\.venv\Scripts\jqcli.exe --format json auth status
```

## Missing pytest

Symptom:

```text
No module named pytest
```

Fix:

```powershell
uv sync --extra test
```

Then rerun:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Stale Cookie Or Login Redirect

Symptoms:

- `auth status` reports authenticated, but live API responses contain `redirect` to `/user/login/index`
- `backtest result` returns a login redirect

Fix:

```powershell
.\.venv\Scripts\jqcli.exe --env-file .env --format json --non-interactive --timeout 30 auth login
```

Then rerun live checks without `--env-file` so the refreshed saved cookie is used:

```powershell
.\codex-skill\jqcli\scripts\smoke_readonly.ps1
```

## System Busy Response

Symptom:

```json
{"status":"2","code":"20000","msg":"系统繁忙，请稍后重试"}
```

Treat this as a live service response, not a local parser failure. Retry once after a short wait. If it persists, report it separately from local test results.

## Research Execution Timeout Or Channel Failure

If `research exec` or `research run` times out or reports a WebSocket/channel error, preserve the original error and let jqcli finish its `finally` cleanup. Do not attach to, interrupt, or delete a kernel/session returned by the read-only list unless it was created by the current invocation.

After the command returns, compare read-only counts with a baseline:

```powershell
.\.venv\Scripts\jqcli.exe --format json --non-interactive research kernels
.\.venv\Scripts\jqcli.exe --format json --non-interactive research sessions
```

Report counts and cleanup status only; do not include identifiers, Notebook paths, code, or output unless the user explicitly requested those details. `research run` never saves execution outputs back to the remote Notebook.

## Local Data Paths

Expected ignored local state:

```text
local/data/
local/experiments/
local/logs/
local/marketing/
local/scripts/
```

If generated data appears at repo root, move it under `local/` and update the generating command or default path.
