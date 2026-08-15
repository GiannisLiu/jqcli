$ErrorActionPreference = "Stop"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$env:PYTHONUTF8 = "1"

function Find-JqcliRepo {
    $Candidates = @()
    if ($env:JQCLI_REPO) {
        $Candidates += $env:JQCLI_REPO
    }
    $Candidates += (Get-Location).Path
    $Candidates += "D:\project\jqcli"

    foreach ($Candidate in $Candidates) {
        if (-not $Candidate) {
            continue
        }
        $Current = Resolve-Path -LiteralPath $Candidate -ErrorAction SilentlyContinue
        while ($Current) {
            $Path = $Current.Path
            $Pyproject = Join-Path $Path "pyproject.toml"
            if ((Test-Path -LiteralPath $Pyproject) -and ((Get-Content -LiteralPath $Pyproject -Raw) -match 'name\s*=\s*"jqcli"')) {
                return $Path
            }
            $Parent = Split-Path -Parent $Path
            if (-not $Parent -or $Parent -eq $Path) {
                break
            }
            $Current = Resolve-Path -LiteralPath $Parent -ErrorAction SilentlyContinue
        }
    }

    throw "jqcli repository not found. Set JQCLI_REPO or run this script from the jqcli workspace."
}

$RepoRoot = Find-JqcliRepo
$Jqcli = Join-Path $RepoRoot ".venv\Scripts\jqcli.exe"
$BaseArgs = @("--format", "json", "--non-interactive", "--timeout", "30")

if (-not (Test-Path -LiteralPath $Jqcli)) {
    throw "jqcli.exe not found at $Jqcli. Run: uv sync --extra test"
}

function Invoke-JqcliJson {
    param([string[]] $Command)

    $PreviousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $Output = & $Jqcli @BaseArgs @Command 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorAction
    }
    if ($ExitCode -ne 0) {
        $Operation = (@($Command) | Select-Object -First 2) -join " "
        throw "jqcli $Operation failed with exit code $ExitCode"
    }
    return ($Output | Out-String | ConvertFrom-Json)
}

$Summary = [ordered]@{}

$Auth = Invoke-JqcliJson @("auth", "status")
$Summary.authenticated = [bool] $Auth.authenticated
$Summary.credential_source = $Auth.credential_source

$Community = Invoke-JqcliJson @("community", "latest", "--page-size", "5", "--max-pages", "1")
$Summary.community_count = @($Community.items).Count
$PostId = if (@($Community.items).Count -gt 0) { [string] $Community.items[0].id } else { $null }
$Summary.community_first_id_present = [bool] $PostId

if ($PostId) {
    $Detail = Invoke-JqcliJson @("community", "detail", $PostId, "--reply-pages", "1")
    $Summary.community_detail_ok = [bool] $Detail.post
}

$Strategies = Invoke-JqcliJson @("strategy", "ls", "--limit", "3")
$Summary.strategy_count = @($Strategies.items).Count
$StrategyId = if (@($Strategies.items).Count -gt 0) { [string] $Strategies.items[0].id } else { $null }
$Summary.strategy_first_id_present = [bool] $StrategyId

if ($StrategyId) {
    $Strategy = Invoke-JqcliJson @("strategy", "show", $StrategyId)
    $Summary.strategy_show_ok = [bool] $Strategy.id

    $Backtests = Invoke-JqcliJson @("backtest", "ls", $StrategyId, "--limit", "3")
    $Summary.backtest_count = @($Backtests.items).Count
    $BacktestId = if (@($Backtests.items).Count -gt 0) { [string] $Backtests.items[0].id } else { $null }
    $Summary.backtest_first_id_present = [bool] $BacktestId

    if ($BacktestId) {
        $Backtest = Invoke-JqcliJson @("backtest", "show", $BacktestId)
        $Stats = Invoke-JqcliJson @("backtest", "stats", $BacktestId)
        $Result = Invoke-JqcliJson @("backtest", "result", $BacktestId)
        $Logs = Invoke-JqcliJson @("backtest", "logs", $BacktestId, "--offset", "0")
        $Summary.backtest_show_ok = [bool] $Backtest.id
        $Summary.backtest_stats_ok = [bool] $Stats.id
        $Summary.backtest_result_ok = [bool] $Result.id
        $Summary.backtest_logs_ok = $null -ne $Logs.logs
    }
}

$Research = $null
try {
    $Research = Invoke-JqcliJson @("research", "ls")
} catch {
    if ($_.Exception.Message -match "exit code 1$") {
        $Summary.research_skipped_not_authenticated = $true
    } else {
        throw
    }
}

if ($null -ne $Research) {
    $Summary.research_count = @($Research.items).Count
    $ResearchPath = if (@($Research.items).Count -gt 0) { [string] $Research.items[0].path } else { $null }
    $Summary.research_first_path_present = [bool] $ResearchPath

    if ($ResearchPath) {
        $ResearchItem = Invoke-JqcliJson @("research", "show", $ResearchPath)
        $Summary.research_show_ok = [bool] $ResearchItem.type
        $Summary.research_content_requested = $false
    }

    $KernelSpecs = Invoke-JqcliJson @("research", "kernelspecs")
    $Kernels = Invoke-JqcliJson @("research", "kernels")
    $Sessions = Invoke-JqcliJson @("research", "sessions")
    $Summary.research_kernelspec_count = [int] $KernelSpecs.total
    $Summary.research_default_kernelspec_present = [bool] $KernelSpecs.default
    $Summary.research_kernel_count = [int] $Kernels.total
    $Summary.research_kernels_query_ok = $null -ne $Kernels.total
    $Summary.research_session_count = [int] $Sessions.total
    $Summary.research_sessions_query_ok = $null -ne $Sessions.total
}

$Cloneable = $null
foreach ($Item in @($Community.items)) {
    $CandidatePostId = [string] $Item.id
    try {
        $CandidateDetail = Invoke-JqcliJson @("community", "detail", $CandidatePostId, "--reply-pages", "1")
    } catch {
        continue
    }
    $Backtest = $CandidateDetail.post.backtest
    if ($Backtest -and $Backtest.id) {
        $Cloneable = [pscustomobject]@{ PostId = $CandidatePostId; BacktestId = [string] $Backtest.id }
        break
    }
}

$Summary.cloneable_post_found = [bool] $Cloneable
if ($Cloneable) {
    $Check = Invoke-JqcliJson @("community", "clone-strategy", $Cloneable.PostId, "--backtest-id", $Cloneable.BacktestId)
    $Summary.clone_strategy_check_ok = $null -ne $Check.execute
    $Summary.clone_strategy_execute_flag = $Check.execute
}

$Summary | ConvertTo-Json -Depth 5
