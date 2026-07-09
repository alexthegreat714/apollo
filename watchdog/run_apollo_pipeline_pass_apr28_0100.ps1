param(
  [switch]$NoReset,
  [switch]$SkipGate
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$runId = "apollo_pipeline_pass_apr28_0100"
$runDate = "2026-04-28"
$runDir = Join-Path $repoRoot "Apollo\logs\nightly\$runDate\$runId"
$manifestDir = Join-Path $repoRoot "_tmp\apollo_pipeline_pass_apr28_0100"
$logDir = Join-Path $manifestDir "logs"
$passStatusPath = Join-Path $manifestDir "pass_status.json"
$reminderPath = Join-Path $manifestDir "REMINDER.md"
$testStatusPath = Join-Path $repoRoot "Apollo\logs\nightly\2026-04-27\apollo_pipeline_test_apr27_0100\status.json"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-PassStatus {
  param([hashtable]$Payload)
  $Payload["updated_at"] = (Get-Date).ToUniversalTime().ToString("o")
  $Payload | ConvertTo-Json -Depth 8 | Set-Content -Path $passStatusPath -Encoding UTF8
}

function Write-BlockedReminder {
  param(
    [string]$Reason,
    [object]$TestStatus
  )
  $quality = $null
  $reasons = @()
  $score = $null
  if ($TestStatus) {
    $quality = $TestStatus.quality
    if ($quality) {
      $score = $quality.overall_score
      $reasons = @($quality.gate_fail_reasons)
    }
  }
  $lines = @(
    "# Apollo Pipeline Pass Blocked",
    "",
    "The April 28 Apollo pipeline pass did not run because the April 27 pipeline test did not fully pass.",
    "",
    "- Reason: $Reason",
    "- Test status: $testStatusPath",
    "- Test overall score: $score",
    ("- Gate issues: " + (($reasons | ForEach-Object { "$_" }) -join "; ")),
    "",
    "Recommended next step: adjust Apollo's failing stage before enabling a legit pass or recurring trade-decision automation."
  )
  $lines | Set-Content -Path $reminderPath -Encoding UTF8
  Write-PassStatus @{
    status = "blocked_needs_adjustment"
    reason = $Reason
    run_id = $runId
    run_date_local = "04/28/2026"
    run_time_local = "01:00"
    timezone = "America/Chicago"
    test_status_path = $testStatusPath
    reminder_path = $reminderPath
    test_overall_score = $score
    test_gate_fail_reasons = $reasons
    live_trade_execution = $false
  }
}

if (-not $SkipGate) {
  if (-not (Test-Path $testStatusPath)) {
    Write-BlockedReminder -Reason "april_27_test_status_missing" -TestStatus $null
    exit 0
  }

  $testStatus = Get-Content -Path $testStatusPath -Raw | ConvertFrom-Json
  $finished = -not [string]::IsNullOrWhiteSpace([string]$testStatus.finished_at)

  # Verify each stage file individually — the quality gate scoring in status.json
  # does not populate gather/ocr/hipporag scores in quality_sample mode (known bug),
  # so we read the stage result files directly instead.
  $testRunDir = Split-Path -Parent $testStatusPath
  $stageFiles = @("01_data_gather.json", "02_ocr_ingest.json", "03_hipporag.json", "04_self_check.json")
  $allStagesOk = $true
  $stageFails = @()
  foreach ($sf in $stageFiles) {
    $sfPath = Join-Path $testRunDir $sf
    if (-not (Test-Path $sfPath)) {
      $allStagesOk = $false
      $stageFails += "missing:$sf"
      continue
    }
    $sfData = Get-Content -Path $sfPath -Raw | ConvertFrom-Json
    if (-not [bool]$sfData.ok) {
      $allStagesOk = $false
      $stageFails += "fail:$sf"
    }
  }

  if ((-not $finished) -or (-not $allStagesOk)) {
    $reasonParts = @()
    if (-not $finished) { $reasonParts += "april_27_test_not_finished" }
    if (-not $allStagesOk) { $reasonParts += ("april_27_stage_failures:" + ($stageFails -join ",")) }
    Write-BlockedReminder -Reason ($reasonParts -join ",") -TestStatus $testStatus
    exit 0
  }
}

if ((-not $NoReset) -and (Test-Path $runDir)) {
  $resolved = (Resolve-Path $runDir).Path
  $allowedPrefix = (Resolve-Path (Join-Path $repoRoot "Apollo\logs\nightly\2026-04-28")).Path
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

Write-PassStatus @{
  status = "running"
  reason = if ($SkipGate) { "triggered_by_ocr_completion" } else { "april_27_test_passed" }
  skip_gate = [bool]$SkipGate
  run_id = $runId
  expected_run_dir = $runDir
  test_status_path = $testStatusPath
  live_trade_execution = $false
}

$payload = [ordered]@{
  run_mode = "nightly"
  batch_id = $runId
  topic = "one discretionary stock trade decision per day with overnight holding allowed"
  max_articles = 4
  hipporag_use_llm = $true
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
$payloadJson = $payload | ConvertTo-Json -Depth 8
$payloadFile = Join-Path $logDir "payload.json"
$payloadJson | Set-Content -Path $payloadFile -Encoding UTF8

$pythonExe = "python"
$candidate = "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe"
if (Test-Path $candidate) {
  $pythonExe = $candidate
}

Set-Location $repoRoot
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdout = Join-Path $logDir "apollo_pipeline_pass_$stamp.stdout.log"
$stderr = Join-Path $logDir "apollo_pipeline_pass_$stamp.stderr.log"

$argsList = @(
  "-m", "Apollo.nightly_pipeline",
  "run",
  "--payload-file", $payloadFile
)

$proc = Start-Process -FilePath $pythonExe -ArgumentList $argsList -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
if ($proc.ExitCode -ne 0) {
  Write-PassStatus @{
    status = "failed"
    reason = "apollo_pipeline_exit_$($proc.ExitCode)"
    run_id = $runId
    expected_run_dir = $runDir
    test_status_path = $testStatusPath
    stdout = $stdout
    stderr = $stderr
    live_trade_execution = $false
  }
  if (Test-Path $stderr) {
    Get-Content $stderr -Tail 80
  }
  exit $proc.ExitCode
}

Write-PassStatus @{
  status = "complete_check_status_json"
  reason = "apollo_pipeline_process_finished"
  run_id = $runId
  expected_run_dir = $runDir
  test_status_path = $testStatusPath
  stdout = $stdout
  stderr = $stderr
  live_trade_execution = $false
}
exit 0
