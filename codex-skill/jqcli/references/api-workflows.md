# jqcli API Workflows

## Simulation Read-Only Workflow

Use `simulation ls` to find the requested simulation by name and obtain a fresh ID. Do not substitute a strategy ID or backtest ID. Commands resolve the detail page's internal ID before reading records.

```powershell
.\.venv\Scripts\jqcli.exe --format json --non-interactive simulation ls
.\.venv\Scripts\jqcli.exe --format json --non-interactive simulation show <simulation_id>
.\.venv\Scripts\jqcli.exe --format json --non-interactive simulation positions <simulation_id>
.\.venv\Scripts\jqcli.exe --format json --non-interactive simulation orders <simulation_id> --start 2026-09-14 --end 2026-09-18
.\.venv\Scripts\jqcli.exe --format json --non-interactive simulation returns <simulation_id>
.\.venv\Scripts\jqcli.exe --format json --non-interactive simulation returns <simulation_id> --today
.\.venv\Scripts\jqcli.exe --format json --non-interactive simulation returns <simulation_id> --date 2026-09-18
```

Replace example dates with the requested interval. `positions` and `orders` default to today's date in UTC+8; `--end` defaults to `--start`. Date intervals include both endpoints. `orders` reads the simulation's strategy-generated order records, preserving submitted quantity/price, order type, status, filled quantity/price, commission and match time. No order creation or cancellation is performed.

For a bounded live smoke check, select one simulation and a short date interval, then write a dedicated local snapshot:

```powershell
.\.venv\Scripts\jqcli.exe --format json --non-interactive simulation sync <simulation_id> --start 2026-09-18 --end 2026-09-19 --output local/data/simulations/smoke.json
```

Omitting the ID syncs all simulations. Omitting `--start` reads holdings and order records from each simulation's start date; full history may require many requests. The existing `smoke_readonly.ps1` covers authentication, strategies, backtests, research metadata and community data; run these simulation checks separately.

`sync` writes a complete replacement snapshot to `local/data/simulations/snapshot.json` by default; it does not merge old data. `--start/--end` constrain holdings and order records only. Historical returns cover the complete available curve, and `today_returns` always uses the execution date. The snapshot also contains the full simulation list and `latest_stats`. Use a separate `--output` when retaining multiple scopes. Failed or truncated syncs leave an existing snapshot intact.

When reporting data:

- Use `historical_returns.items` (or `returns.items`) for cumulative return charts; `return` and `benchmark_return` are percentages (`2.3` means `2.3%`), and `time` is Unix milliseconds. Format timestamps in UTC+8.
- Latest statistics are under `latest_stats.data.stat`; each metric has `time` and `value` arrays. Fields such as `algorithm_return`, `annual_algo_return`, `max_drawdown`, `intraday_return` and `monthly_return` are decimal ratios. Statistics can have timestamp `0`; date the report from the latest curve point, and do not claim a verified statistics timestamp from `0`.
- Never call the latest `intraday_return` today's return unless today's data supports it. Non-trading days may have empty `today_returns.items`; daily-frequency simulations may have no intraday curve. Do not replace missing values with zero.
- For latest holdings, report `record_dates` and row `time`, not merely the requested `date`. A weekend query can return the prior trading day's holdings. Preserve cash and total assets in `days[].data`.
- Check `complete`. Per-day `isLimit=true` means the server truncated holdings or order records; sync rejects these snapshots. Do not claim a complete history from capped data.

Targeted local verification:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api/test_simulation.py tests/test_commands/test_simulation.py -q
```

## Local Tests

Run all tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run API-only tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_api -q
```

## Read-Only Live Smoke Check

Use this before live write checks:

```powershell
.\codex-skill\jqcli\scripts\smoke_readonly.ps1
```

It checks:

- `auth status`
- `community latest`
- `community detail`
- `strategy ls`
- if available, `strategy show`
- if available, `backtest ls/show/stats/result/logs`
- `research ls`
- if available, metadata-only `research show`
- `research kernelspecs`
- `research kernels`
- `research sessions`
- `community clone-strategy` in non-executing check mode when a post with a backtest is found

The script reports only counts and booleans for research checks; it does not echo user paths, file names, contents, kernel/session identifiers, or kernel-spec names. It never calls `research exec` or `research run`.

## Write Live Smoke Check

Run only with user approval:

```powershell
.\codex-skill\jqcli\scripts\smoke_write_compile.ps1
```

It creates a temporary strategy named `jqcli_skill_smoke_<timestamp>`, edits it, runs a compile-only backtest, reads result/logs, deletes the compile record, and deletes the temporary strategy.

## Useful Direct Commands

Authentication:

```powershell
.\.venv\Scripts\jqcli.exe --format json --non-interactive auth status
.\.venv\Scripts\jqcli.exe --env-file .env --format json --non-interactive auth login
```

Community:

```powershell
.\.venv\Scripts\jqcli.exe --format json --non-interactive community latest --page-size 3 --max-pages 1
.\.venv\Scripts\jqcli.exe --format json --non-interactive community detail <post_id> --reply-pages 1
```

Strategies:

```powershell
.\.venv\Scripts\jqcli.exe --format json --non-interactive strategy ls --limit 3
.\.venv\Scripts\jqcli.exe --format json --non-interactive strategy show <strategy_id>
```

Backtests:

```powershell
.\.venv\Scripts\jqcli.exe --format json --non-interactive backtest ls <strategy_id> --limit 3
.\.venv\Scripts\jqcli.exe --format json --non-interactive backtest show <backtest_id>
.\.venv\Scripts\jqcli.exe --format json --non-interactive backtest stats <backtest_id>
.\.venv\Scripts\jqcli.exe --format json --non-interactive backtest result <backtest_id>
.\.venv\Scripts\jqcli.exe --format json --non-interactive backtest logs <backtest_id> --offset 0
```

Research (read-only):

```powershell
.\.venv\Scripts\jqcli.exe --format json --non-interactive research ls
.\.venv\Scripts\jqcli.exe --format json --non-interactive research show <remote_path>
.\.venv\Scripts\jqcli.exe --format json --non-interactive research kernelspecs
.\.venv\Scripts\jqcli.exe --format json --non-interactive research kernels
.\.venv\Scripts\jqcli.exe --format json --non-interactive research sessions
```

Keep `research show` metadata-only during live smoke checks; do not pass `--content` unless the user explicitly asks to read that file.

The kernel/session discovery commands are read-only. Summarize only their counts and success/default-present booleans; do not print identifiers, paths, or kernel-spec names.

Research mutations are never part of the standard smoke check. Only when the user explicitly identifies or approves a target:

```powershell
.\.venv\Scripts\jqcli.exe --format json --non-interactive research upload <local_path> <remote_path> --yes
.\.venv\Scripts\jqcli.exe --format json --non-interactive research mkdir <remote_path> --yes
.\.venv\Scripts\jqcli.exe --format json --non-interactive research mv <source> <destination> --yes
.\.venv\Scripts\jqcli.exe --format json --non-interactive research rm <remote_path> --yes
```

Use `--force` with `research upload` only when the user explicitly approves overwriting the existing remote path.

## Research Remote Execution

Remote execution is never part of a standard smoke check. Run it only when the user explicitly asks for or approves remote execution, because executed code may modify files, access the network, or consume remote resources.

Execute a local code file or explicit stdin payload:

```powershell
.\.venv\Scripts\jqcli.exe --format json --non-interactive research exec --file <local.py> --yes
Get-Content -Raw -Encoding utf8 <local.py> | .\.venv\Scripts\jqcli.exe --format json --non-interactive research exec --code-stdin --yes
```

Execute an explicitly identified remote Notebook, optionally selecting zero-based cells:

```powershell
.\.venv\Scripts\jqcli.exe --format json --non-interactive research run <remote.ipynb> --yes
.\.venv\Scripts\jqcli.exe --format json --non-interactive research run <remote.ipynb> --cell 0 --cell 2 --yes
```

Optional execution controls:

```text
--kernel <name>
--execution-timeout <seconds>
--stream
```

`--stream` requires `--format json`. It emits one JSON object per line in arrival order and always finishes with `{"event":"done","result":{...}}` on success.

Always pass `--yes`; interactive mode does not replace this approval. Both commands use a new exclusive high-entropy temporary session and kernel; the `exec` session is only for reliable ownership and cleanup and does not save a research file. Allow the command to reach its cleanup path. `research run` reads the explicitly named Notebook but does not save outputs back to it. After an interrupted or failed live validation, rerun the read-only `research kernels` and `research sessions` commands and compare counts with the pre-run baseline; do not stop unrelated existing objects.
