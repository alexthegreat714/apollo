param()
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "scheduler_state.ps1")

$repoRoot = "C:\Users\blyth\Desktop\Engineering"
$apolloRoot = Join-Path $repoRoot "Apollo"
$outDir = Join-Path $apolloRoot "logs\nightly\overnight_gate_checks"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$jsonPath = Join-Path $outDir "overnight_gate_check_$stamp.json"
$mdPath = Join-Path $outDir "overnight_gate_check_$stamp.md"

function Read-JsonObject {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json } catch { return $null }
}

function Get-IntValue {
    param($Value)
    try { return [int]$Value } catch { return 0 }
}

function Get-AgeMinutes {
    param([string]$Timestamp)
    if ([string]::IsNullOrWhiteSpace($Timestamp)) { return 999999.0 }
    try { return ((Get-Date).ToUniversalTime() - [DateTimeOffset]::Parse($Timestamp).UtcDateTime).TotalMinutes } catch { return 999999.0 }
}

function Get-ProcessRecord {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $null }
    return Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
}

function Test-ProcessIdentity {
    param([object]$ProcessRecord, [object[]]$ExpectedTokens)
    if ($null -eq $ProcessRecord) { return $false }
    return Test-ApolloCommandIdentity -CommandLine ([string]$ProcessRecord.CommandLine) -ExpectedTokens $ExpectedTokens
}

$nightlyStatusPath = Join-Path $apolloRoot "logs\nightly\latest_status.json"
$nightly = Read-JsonObject $nightlyStatusPath
$stageDetails = if ($nightly -and $nightly.stage_details) { $nightly.stage_details } else { $null }
$gatherDetails = if ($stageDetails -and $stageDetails.gather) { $stageDetails.gather } else { $null }
$hippoDetails = if ($stageDetails -and $stageDetails.hipporag) { $stageDetails.hipporag } else { $null }

$gatheredSources = Get-IntValue $(if ($nightly.gathered_sources -ne $null) { $nightly.gathered_sources } elseif ($gatherDetails) { $gatherDetails.accepted_sources } else { 0 })
$newTriples = Get-IntValue $(if ($nightly.new_triples -ne $null) { $nightly.new_triples } elseif ($hippoDetails) { $hippoDetails.triples } else { 0 })

$tradeRoot = Join-Path $apolloRoot "logs\trade_cycle"
$latestTradeDir = Get-ChildItem -LiteralPath $tradeRoot -Directory -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$tradeStatusPath = if ($latestTradeDir) { Join-Path $latestTradeDir.FullName "status.json" } else { "" }
$tradeStatus = if ($tradeStatusPath) { Read-JsonObject $tradeStatusPath } else { $null }
$riskPath = if ($latestTradeDir) { Join-Path $latestTradeDir.FullName "04_risk.json" } else { "" }
$simulatePath = if ($latestTradeDir) { Join-Path $latestTradeDir.FullName "05_simulate.json" } else { "" }
$reportPath = if ($latestTradeDir) { Join-Path $latestTradeDir.FullName "observation_report.md" } else { "" }

$tradeReady = [bool]($tradeStatus -and $tradeStatus.trade_ready)
$simStatus = if ($tradeStatus) { [string]$tradeStatus.sim_status } else { "" }
$topTicker = if ($tradeStatus) { [string]$tradeStatus.top_ticker } else { "" }

$schedulerManifestPath = Join-Path $apolloRoot "logs\nightly\scheduler_runs\latest_scheduler_run.json"
$schedulerManifest = Read-JsonObject $schedulerManifestPath
$nightlyLockPath = Join-Path $apolloRoot "logs\nightly\nightly.lock"
$nightlyLock = Read-JsonObject $nightlyLockPath
$task = Get-ScheduledTask -TaskName "ApolloNightlyPipeline0100" -ErrorAction SilentlyContinue
$taskInfo = if ($task) { $task | Get-ScheduledTaskInfo } else { $null }
$manifestState = if ($schedulerManifest) { [string]$schedulerManifest.state } else { "missing" }
$heartbeatAge = if ($schedulerManifest) { Get-AgeMinutes ([string]$schedulerManifest.heartbeat_at) } else { 999999.0 }
$lockHeartbeat = if ($nightlyLock) { [string]$nightlyLock.heartbeat_at } else { "" }
if (-not $lockHeartbeat -and $nightlyLock) { $lockHeartbeat = [string]$nightlyLock.started_at }
$lockAge = Get-AgeMinutes $lockHeartbeat
$lockStale = (-not $nightlyLock) -or ($lockAge -gt 15)
$processRecord = if ($schedulerManifest) { Get-ProcessRecord -ProcessId ([int]$schedulerManifest.child_pid) } else { $null }
$processIdentityMatches = if ($schedulerManifest) { Test-ProcessIdentity -ProcessRecord $processRecord -ExpectedTokens @($schedulerManifest.expected_command_tokens) } else { $false }
$decision = Get-ApolloSchedulerDecision `
    -ManifestState $manifestState `
    -HeartbeatAgeMinutes $heartbeatAge `
    -LockStale $lockStale `
    -ProcessExists ($null -ne $processRecord) `
    -ProcessIdentityMatches $processIdentityMatches `
    -TaskState $(if ($task) { [string]$task.State } else { "missing" })
$hung = [bool]$decision.hung
$recoveryAction = "none"
if ($hung) {
    if ($decision.may_terminate) {
        & taskkill.exe /PID ([int]$schedulerManifest.child_pid) /T /F | Out-Null
        $recoveryAction = "terminated_exact_recorded_child_tree"
    } elseif ($processRecord) {
        $recoveryAction = "alert_identity_mismatch_no_termination"
    } else {
        $recoveryAction = "alert_recorded_child_missing"
    }
}
$schedulerConsistent = [bool]$decision.consistent

$payload = [ordered]@{
    ok = (($gatheredSources -gt 0) -and ($newTriples -gt 0) -and $schedulerConsistent -and -not $hung)
    checked_at = (Get-Date).ToUniversalTime().ToString("o")
    nightly = [ordered]@{
        status_path = $nightlyStatusPath
        run_id = if ($nightly) { [string]$nightly.run_id } else { "" }
        finished_at = if ($nightly) { [string]$nightly.finished_at } else { "" }
        gathered_sources = $gatheredSources
        new_triples = $newTriples
        ok = if ($nightly) { [bool]$nightly.ok } else { $false }
    }
    scheduler = [ordered]@{
        task_name = "ApolloNightlyPipeline0100"
        task_state = if ($task) { [string]$task.State } else { "missing" }
        last_run_time = if ($taskInfo) { $taskInfo.LastRunTime.ToString("o") } else { "" }
        last_task_result = if ($taskInfo) { [int]$taskInfo.LastTaskResult } else { $null }
        manifest_path = $schedulerManifestPath
        manifest_state = $manifestState
        heartbeat_age_minutes = [math]::Round($heartbeatAge, 2)
        lock_heartbeat_age_minutes = [math]::Round($lockAge, 2)
        lock_stale = $lockStale
        child_pid = if ($schedulerManifest) { [int]$schedulerManifest.child_pid } else { 0 }
        child_running = ($null -ne $processRecord)
        process_identity_matches = $processIdentityMatches
        hung = [bool]$hung
        recovery_action = $recoveryAction
        consistent = $schedulerConsistent
    }
    trade_cycle = [ordered]@{
        run_dir = if ($latestTradeDir) { [string]$latestTradeDir.FullName } else { "" }
        status_path = $tradeStatusPath
        risk_path = $riskPath
        simulate_path = $simulatePath
        observation_report = $reportPath
        trade_ready = $tradeReady
        top_ticker = $topTicker
        sim_status = $simStatus
        first_valid_simulation_trade_output = if ($tradeReady -and (Test-Path -LiteralPath $simulatePath)) { $simulatePath } else { "" }
    }
}

$payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

$lines = @(
    "# Apollo Overnight Gate Check",
    "",
    "- checked_at: $($payload.checked_at)",
    "- gathered_sources: $gatheredSources",
    "- new_triples: $newTriples",
    "- nightly_gate_ok: $($payload.ok)",
    "- scheduler_consistent: $schedulerConsistent",
    "- scheduler_manifest_state: $manifestState",
    "- scheduler_heartbeat_age_minutes: $([math]::Round($heartbeatAge, 2))",
    "- scheduler_hung: $hung",
    "- scheduler_recovery_action: $recoveryAction",
    "- trade_ready: $tradeReady",
    "- top_ticker: $topTicker",
    "- sim_status: $simStatus",
    "- first_valid_simulation_trade_output: $($payload.trade_cycle.first_valid_simulation_trade_output)",
    "",
    "## Files",
    "- nightly_status: $nightlyStatusPath",
    "- trade_status: $tradeStatusPath",
    "- risk: $riskPath",
    "- simulation: $simulatePath",
    "- observation_report: $reportPath"
)
$lines -join [Environment]::NewLine | Set-Content -LiteralPath $mdPath -Encoding UTF8

Write-Host "Apollo overnight gate check written:"
Write-Host $jsonPath
Write-Host $mdPath
if (-not $payload.ok) { exit 1 }
exit 0
