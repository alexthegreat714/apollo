param(
  [switch]$NoReset
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$runId = "apollo_nightly_pipeline0100_apr29"
$runDate = "2026-04-29"
$runDir = Join-Path $repoRoot "Apollo\logs\nightly\$runDate\$runId"
$manifestDir = Join-Path $repoRoot "_tmp\apollo_nightly_pipeline0100_apr29"
$logDir = Join-Path $manifestDir "logs"
$statusPath = Join-Path $manifestDir "promotion_status.json"
$reminderPath = Join-Path $manifestDir "REMINDER.md"
$passStatusPath = Join-Path $repoRoot "Apollo\logs\nightly\2026-04-28\apollo_pipeline_pass_apr28_0100\status.json"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-PromotionStatus {
  param([hashtable]$Payload)
  $Payload["updated_at"] = (Get-Date).ToUniversalTime().ToString("o")
  $Payload | ConvertTo-Json -Depth 8 | Set-Content -Path $statusPath -Encoding UTF8
}

function Write-BlockedReminder {
  param(
    [string]$Reason,
    [object]$PassStatus
  )
  $quality = $null
  $reasons = @()
  $score = $null
  if ($PassStatus) {
    $quality = $PassStatus.quality
    if ($quality) {
      $score = $quality.overall_score
      $reasons = @($quality.gate_fail_reasons)
    }
  }
  $lines = @(
    "# ApolloNightlyPipeline0100 Update Blocked",
    "",
    "ApolloNightlyPipeline0100 did not run because the April 28 conditional Apollo pass did not fully pass.",
    "",
    "- Reason: $Reason",
    "- Pass status: $passStatusPath",
    "- Pass overall score: $score",
    ("- Gate issues: " + (($reasons | ForEach-Object { "$_" }) -join "; ")),
    "",
    "Recommended next step: adjust Apollo's failing stage before restoring the normal ApolloNightlyPipeline0100 cadence."
  )
  $lines | Set-Content -Path $reminderPath -Encoding UTF8
  Write-PromotionStatus @{
    status = "blocked_needs_adjustment"
    reason = $Reason
    task_name = "ApolloNightlyPipeline0100"
    run_id = $runId
    run_date_local = "04/29/2026"
    run_time_local = "01:00"
    timezone = "America/Chicago"
    pass_status_path = $passStatusPath
    reminder_path = $reminderPath
    pass_overall_score = $score
    pass_gate_fail_reasons = $reasons
    live_trade_execution = $false
  }
}

if (-not (Test-Path $passStatusPath)) {
  Write-BlockedReminder -Reason "april_28_pass_status_missing" -PassStatus $null
  exit 0
}

$passStatus = Get-Content -Path $passStatusPath -Raw | ConvertFrom-Json
$passOk = [bool]$passStatus.ok
$gatePass = $false
if ($passStatus.quality) {
  $gatePass = [bool]$passStatus.quality.gate_pass
}
$finished = -not [string]::IsNullOrWhiteSpace([string]$passStatus.finished_at)
if ((-not $passOk) -or (-not $gatePass) -or (-not $finished)) {
  $reasonParts = @()
  if (-not $finished) { $reasonParts += "april_28_pass_not_finished" }
  if (-not $passOk) { $reasonParts += "april_28_pass_ok_false" }
  if (-not $gatePass) { $reasonParts += "april_28_quality_gate_false" }
  Write-BlockedReminder -Reason ($reasonParts -join ",") -PassStatus $passStatus
  exit 0
}

if ((-not $NoReset) -and (Test-Path $runDir)) {
  $resolved = (Resolve-Path $runDir).Path
  $allowedPrefix = (Resolve-Path (Join-Path $repoRoot "Apollo\logs\nightly\2026-04-29")).Path
  if ($resolved.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $resolved -Recurse -Force
  } else {
    throw "refusing_to_remove_unexpected_run_dir:$resolved"
  }
}

try {
  [System.Diagnostics.Process]::GetCurrentProcess().PriorityClass = [System.Diagnostics.ProcessPriorityClass]::BelowNormal
} catch {
  # Priority adjustment is best-effort only.
}

$env:OMP_NUM_THREADS = "4"
$env:OPENBLAS_NUM_THREADS = "4"
$env:MKL_NUM_THREADS = "4"
$env:NUMEXPR_NUM_THREADS = "4"
$env:OPENCV_FOR_THREADS_NUM = "4"

Write-PromotionStatus @{
  status = "running"
  reason = "april_28_pass_succeeded"
  task_name = "ApolloNightlyPipeline0100"
  run_id = $runId
  expected_run_dir = $runDir
  pass_status_path = $passStatusPath
  live_trade_execution = $false
}

$payload = [ordered]@{
  run_mode = "nightly"
  batch_id = $runId
  topic = "one discretionary stock trade decision per day with overnight holding allowed"
  max_articles = 4
  hipporag_use_llm = $true
  hipporag_llm_model = "gemma3:12b"
  allow_cpu_fallback = $false
  focus_universe_enabled = $true
  focus_universe_gb10_only = $true
  focus_universe_decision_min_volume = 60
  strict_stage_min_score = 70
  gather_allow_seed_fallback = $false
  gather_web_rate_limit_retries = 1
  ocr_route_mode = "quality_first"
  ocr_require_finance_structure = $true
  ocr_stage_min_score = 85
  memory_guard_enabled = $true
  memory_min_available_mb = 4096
  memory_max_used_percent = 92
  memory_max_vmmem_mb = 12288
  memory_max_ollama_runners = 4
  preflight_max_wait_sec = 1800
  overall_timeout_sec = 28800
}
$payloadPath = Join-Path $logDir "apollo_nightly0100_payload.json"
$payload | ConvertTo-Json -Depth 8 | Set-Content -Path $payloadPath -Encoding UTF8

$pythonExe = "python"
$candidate = "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe"
if (Test-Path $candidate) {
  $pythonExe = $candidate
}

Set-Location $repoRoot
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdout = Join-Path $logDir "apollo_nightly0100_$stamp.stdout.log"
$stderr = Join-Path $logDir "apollo_nightly0100_$stamp.stderr.log"
$argsList = @("-m", "Apollo.nightly_pipeline", "run", "--payload-file", $payloadPath)
$proc = Start-Process -FilePath $pythonExe -ArgumentList $argsList -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
if ($proc.ExitCode -ne 0) {
  Write-PromotionStatus @{
    status = "failed"
    reason = "apollo_nightly0100_exit_$($proc.ExitCode)"
    task_name = "ApolloNightlyPipeline0100"
    run_id = $runId
    expected_run_dir = $runDir
    pass_status_path = $passStatusPath
    stdout = $stdout
    stderr = $stderr
    live_trade_execution = $false
  }
  if (Test-Path $stderr) {
    Get-Content $stderr -Tail 80
  }
  exit $proc.ExitCode
}

Write-PromotionStatus @{
  status = "complete_check_status_json"
  reason = "apollo_nightly0100_process_finished"
  task_name = "ApolloNightlyPipeline0100"
  run_id = $runId
  expected_run_dir = $runDir
  pass_status_path = $passStatusPath
  stdout = $stdout
  stderr = $stderr
  live_trade_execution = $false
}
exit 0
