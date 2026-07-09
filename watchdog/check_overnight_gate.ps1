param()
$ErrorActionPreference = "Stop"

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

$payload = [ordered]@{
    ok = (($gatheredSources -gt 0) -and ($newTriples -gt 0))
    checked_at = (Get-Date).ToUniversalTime().ToString("o")
    nightly = [ordered]@{
        status_path = $nightlyStatusPath
        run_id = if ($nightly) { [string]$nightly.run_id } else { "" }
        finished_at = if ($nightly) { [string]$nightly.finished_at } else { "" }
        gathered_sources = $gatheredSources
        new_triples = $newTriples
        ok = if ($nightly) { [bool]$nightly.ok } else { $false }
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
