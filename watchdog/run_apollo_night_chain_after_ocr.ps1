param(
  [string]$OcrTaskName,
  [string]$RunId = "",
  [string]$RunDate = "",
  [string]$Tickers = "NVDA,AMD,AVGO,MSFT,AMZN",
  [int]$MaxWaitMinutes = 540,
  [int]$PollSeconds = 60,
  [int]$GraphMaxNewChunks = 1200,
  [int]$GraphTimeoutSeconds = 2400,
  [switch]$StartNextOcrAfterChain,
  [string]$NextOcrRunner = "",
  [string]$NextOcrRunDir = "",
  [string]$NextOcrRunId = "",
  [int]$NextOcrBroadLimit = 0,
  [int]$NextOcrFocusLimit = 20,
  [switch]$Notify
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($OcrTaskName)) {
  throw "OcrTaskName is required"
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
if ([string]::IsNullOrWhiteSpace($RunDate)) {
  $RunDate = (Get-Date).ToString("yyyy-MM-dd")
}
if ([string]::IsNullOrWhiteSpace($RunId)) {
  $RunId = "apollo_night_chain_$($RunDate.Replace('-', ''))"
}

$logDir = Join-Path $repoRoot "Apollo\logs\night_chain\$RunDate\$RunId"
$statusPath = Join-Path $logDir "status.json"
$lockPath = Join-Path $repoRoot "Apollo\logs\night_chain\apollo_night_chain.lock"
$skyHandoffPath = Join-Path $repoRoot "Sky\logs\overnight_run_status.json"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-ChainStatus {
  param([hashtable]$Payload)
  if (-not $Payload.ContainsKey("state")) {
    $statusText = ""
    if ($Payload.ContainsKey("status") -and $null -ne $Payload["status"]) {
      $statusText = [string]$Payload["status"]
    }
    $state = switch -Regex ($statusText) {
      "^complete$" { "complete"; break }
      "^complete_with_failures$" { "failed"; break }
      "^failed$" { "failed"; break }
      "^skipped" { "incomplete"; break }
      "^waiting|^running" { "running"; break }
      default { "running" }
    }
    $Payload["state"] = $state
  }
  if (-not $Payload.ContainsKey("message")) {
    $messageText = "apollo_night_chain"
    if ($Payload.ContainsKey("status") -and $null -ne $Payload["status"]) {
      $messageText = [string]$Payload["status"]
    }
    $Payload["message"] = $messageText
  }
  $Payload["updated_at"] = (Get-Date).ToUniversalTime().ToString("o")
  $Payload | ConvertTo-Json -Depth 12 | Set-Content -Path $statusPath -Encoding UTF8
}

function Write-SkyHandoff {
  param(
    [hashtable]$ApolloPayload,
    [object]$OcrPayload = $null,
    [object]$NextOcrPayload = $null
  )
  try {
    New-Item -ItemType Directory -Force -Path (Split-Path $skyHandoffPath) | Out-Null
    $current = @{}
    if (Test-Path $skyHandoffPath) {
      try { $current = Get-Content -Raw $skyHandoffPath | ConvertFrom-Json -AsHashtable } catch { $current = @{} }
    }
    $current["date"] = (Get-Date).ToString("yyyy-MM-dd")
    $current["updated_at"] = (Get-Date).ToUniversalTime().ToString("o")
    if ($OcrPayload) { $current["ocr97"] = $OcrPayload }
    $current["apollo"] = $ApolloPayload
    $current["apollo_chain"] = $ApolloPayload
    if ($NextOcrPayload) { $current["next_ocr97"] = $NextOcrPayload }
    $current | ConvertTo-Json -Depth 12 | Set-Content -Path $skyHandoffPath -Encoding UTF8
  } catch {
  }
}

function Send-ArgusAlert {
  param(
    [string]$Level,
    [string]$Title,
    [string]$Message,
    [string]$Event
  )
  if (-not $Notify) { return }
  $payload = @{
    level = $Level
    title = $Title
    message = $Message
    tags = @("apollo", "night-chain")
    source = "apollo"
    event = $Event
  } | ConvertTo-Json -Depth 6
  try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5216/alerts/external" -ContentType "application/json" -Body $payload -TimeoutSec 20 | Out-Null
  } catch {
  }
}

function Get-TaskSnapshot {
  param([string]$Name)
  try {
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction Stop
    $info = Get-ScheduledTaskInfo -TaskName $Name -ErrorAction Stop
    return [ordered]@{
      found = $true
      state = [string]$task.State
      last_run_time = $info.LastRunTime
      last_task_result = [int]$info.LastTaskResult
      next_run_time = $info.NextRunTime
    }
  } catch {
    return [ordered]@{
      found = $false
      state = "missing"
      last_run_time = $null
      last_task_result = $null
      next_run_time = $null
      error = $_.Exception.Message
    }
  }
}

function Invoke-PythonStep {
  param(
    [string]$StepName,
    [string[]]$Arguments
  )
  $pythonExe = "python"
  $candidate = "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe"
  if (Test-Path $candidate) { $pythonExe = $candidate }
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $stdout = Join-Path $logDir "$StepName`_$stamp.stdout.log"
  $stderr = Join-Path $logDir "$StepName`_$stamp.stderr.log"
  $exitCode = 1
  $exceptionMessage = ""
  Push-Location $repoRoot
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    & $pythonExe @Arguments > $stdout 2> $stderr
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
  } catch {
    $exitCode = 1
    $exceptionMessage = $_.Exception.Message
    try {
      Add-Content -Path $stderr -Value $exceptionMessage -Encoding UTF8
    } catch {
    }
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
    Pop-Location
  }
  return [ordered]@{
    step = $StepName
    exit_code = $exitCode
    ok = ($exitCode -eq 0)
    stdout = $stdout
    stderr = $stderr
    exception = $exceptionMessage
    completed_at = (Get-Date).ToUniversalTime().ToString("o")
  }
}

if (Test-Path $lockPath) {
  $age = (Get-Date) - (Get-Item $lockPath).LastWriteTime
  if ($age.TotalHours -lt 14) {
    Write-ChainStatus @{
      status = "skipped_active_lock"
      run_id = $RunId
      lock_path = $lockPath
      lock_age_minutes = [math]::Round($age.TotalMinutes, 1)
      live_trade_execution = $false
    }
    exit 0
  }
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}
"pid=$PID started=$(Get-Date -Format o)" | Set-Content -Path $lockPath -Encoding UTF8

try {
  [System.Diagnostics.Process]::GetCurrentProcess().PriorityClass = [System.Diagnostics.ProcessPriorityClass]::BelowNormal
} catch {}

$ocr = Get-TaskSnapshot -Name $OcrTaskName
$deadline = (Get-Date).AddMinutes($MaxWaitMinutes)
Write-ChainStatus @{
  status = "waiting_for_ocr"
  run_id = $RunId
  ocr_task = $OcrTaskName
  ocr = $ocr
  live_trade_execution = $false
}
$initialOcrMissing = -not [bool]$ocr.found

try {
  if ($initialOcrMissing) {
    throw "ocr_task_missing:$OcrTaskName"
  }
  while ((Get-Date) -lt $deadline) {
    $ocr = Get-TaskSnapshot -Name $OcrTaskName
    if (-not [bool]$ocr.found) {
      throw "ocr_task_missing:$OcrTaskName"
    }
    if ([string]$ocr.state -eq "Running") {
      Write-ChainStatus @{
        status = "waiting_for_ocr"
        run_id = $RunId
        ocr_task = $OcrTaskName
        ocr = $ocr
        live_trade_execution = $false
      }
      Start-Sleep -Seconds $PollSeconds
      continue
    }
    if ($ocr.last_run_time -and ([datetime]$ocr.last_run_time).Year -ge 2026) {
      break
    }
    Start-Sleep -Seconds $PollSeconds
  }
  if (-not ($ocr.last_run_time -and ([datetime]$ocr.last_run_time).Year -ge 2026)) {
    throw "ocr_wait_timeout_after_${MaxWaitMinutes}_minutes"
  }

  $ocrStatus = if ([int]$ocr.last_task_result -eq 0) { "ocr_complete" } else { "ocr_complete_nonzero" }
  $steps = @()

  Write-ChainStatus @{
    status = "running_phase2_hipporag"
    run_id = $RunId
    ocr_task = $OcrTaskName
    ocr = $ocr
    steps = $steps
    live_trade_execution = $false
  }
  $phase2Args = @(
    "-m", "Apollo.homework_phase2_pipeline",
    "run",
    "--tickers", $Tickers,
    "--upstream-ocr-task", $OcrTaskName,
    "--upstream-ocr-status", $ocrStatus,
    "--upstream-ocr-result", ([string]$ocr.last_task_result),
    "--upstream-ocr-completed-at", ([datetime]$ocr.last_run_time).ToUniversalTime().ToString("o"),
    "--graph-max-new-chunks", ([string]$GraphMaxNewChunks),
    "--graph-timeout-sec", ([string]$GraphTimeoutSeconds)
  )
  if ($Notify) { $phase2Args += "--notify" }
  $steps += Invoke-PythonStep -StepName "phase2_hipporag" -Arguments $phase2Args

  Write-ChainStatus @{
    status = "running_swing_trade_study"
    run_id = $RunId
    ocr_task = $OcrTaskName
    ocr = $ocr
    steps = $steps
    live_trade_execution = $false
  }
  $swingArgs = @("-m", "Apollo.swing_study", "run", "--tickers", $Tickers)
  if ($Notify) { $swingArgs += "--notify" }
  $steps += Invoke-PythonStep -StepName "swing_trade_study" -Arguments $swingArgs

  Write-ChainStatus @{
    status = "running_hippograph_build"
    run_id = $RunId
    ocr_task = $OcrTaskName
    ocr = $ocr
    steps = $steps
    live_trade_execution = $false
  }
  $steps += Invoke-PythonStep -StepName "hippograph_build" -Arguments @(
    "-m", "Apollo.apollo_hipporag.build_graph",
    "--rag-dir", (Join-Path $repoRoot "Apollo\chroma_db"),
    "--collection", "apollo_financial",
    "--max-new-chunks", ([string]$GraphMaxNewChunks),
    "--batch-size", "25"
  )

  Write-ChainStatus @{
    status = "running_nightly_pipeline"
    run_id = $RunId
    ocr_task = $OcrTaskName
    ocr = $ocr
    steps = $steps
    live_trade_execution = $false
  }
  $payload = [ordered]@{
    run_mode = "nightly"
    batch_id = "${RunId}_nightly"
    topic = "human-supervised swing trading research with overnight holding allowed"
    max_articles = 4
    hipporag_use_llm = $true
    hipporag_llm_model = "gemma3:12b"
    allow_cpu_fallback = $false
    focus_universe_enabled = $true
    focus_universe_gb10_only = $true
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
    overall_timeout_sec = 21600
  }
  $payloadPath = Join-Path $logDir "apollo_nightly_payload.json"
  $payload | ConvertTo-Json -Depth 8 | Set-Content -Path $payloadPath -Encoding UTF8
  $steps += Invoke-PythonStep -StepName "apollo_nightly" -Arguments @("-m", "Apollo.nightly_pipeline", "run", "--payload-file", $payloadPath)

  Write-ChainStatus @{
    status = "running_mock_swing_trade_prep"
    run_id = $RunId
    ocr_task = $OcrTaskName
    ocr = $ocr
    steps = $steps
    live_trade_execution = $false
  }
  $practiceArgs = @(
    "-m", "Apollo.homework_trade_pipeline",
    "run",
    "--tickers", $Tickers,
    "--upstream-ocr-task", $OcrTaskName,
    "--upstream-ocr-status", $ocrStatus,
    "--upstream-ocr-result", ([string]$ocr.last_task_result),
    "--upstream-ocr-completed-at", ([datetime]$ocr.last_run_time).ToUniversalTime().ToString("o")
  )
  if ($Notify) { $practiceArgs += "--notify" }
  $steps += Invoke-PythonStep -StepName "mock_swing_trade_prep" -Arguments $practiceArgs

  $nextOcr = $null
  if ($StartNextOcrAfterChain) {
    if ([string]::IsNullOrWhiteSpace($NextOcrRunner)) {
      $NextOcrRunner = Join-Path $repoRoot "ocr97\tools\run_mixed_corpus_overnight_scheduled.ps1"
    }
    if ([string]::IsNullOrWhiteSpace($NextOcrRunId)) {
      $NextOcrRunId = "ocr97_post_apollo_$($RunDate.Replace('-', ''))"
    }
    if ([string]::IsNullOrWhiteSpace($NextOcrRunDir)) {
      $NextOcrRunDir = ".\_tmp\$NextOcrRunId"
    }
    Write-ChainStatus @{
      status = "running_next_ocr97"
      run_id = $RunId
      ocr_task = $OcrTaskName
      ocr = $ocr
      steps = $steps
      next_ocr = @{
        runner = $NextOcrRunner
        run_dir = $NextOcrRunDir
        run_id = $NextOcrRunId
      }
      live_trade_execution = $false
    }
    $nextOcrArgs = @(
      "-NoProfile", "-ExecutionPolicy", "Bypass",
      "-File", $NextOcrRunner,
      "-RunDir", $NextOcrRunDir,
      "-RunId", $NextOcrRunId,
      "-BroadLimit", ([string]$NextOcrBroadLimit),
      "-FocusLimit", ([string]$NextOcrFocusLimit)
    )
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $nextStdout = Join-Path $logDir "next_ocr97_$stamp.stdout.log"
    $nextStderr = Join-Path $logDir "next_ocr97_$stamp.stderr.log"
    $nextProc = Start-Process -FilePath powershell.exe -ArgumentList $nextOcrArgs -WorkingDirectory $repoRoot -NoNewWindow -Wait -PassThru -RedirectStandardOutput $nextStdout -RedirectStandardError $nextStderr
    $nextOcr = [ordered]@{
      ok = ($nextProc.ExitCode -eq 0)
      status = if ($nextProc.ExitCode -eq 0) { "complete" } else { "failed" }
      exit_code = $nextProc.ExitCode
      run_id = $NextOcrRunId
      run_dir = $NextOcrRunDir
      stdout = $nextStdout
      stderr = $nextStderr
      completed_at = (Get-Date).ToUniversalTime().ToString("o")
      live_trade_execution = $false
    }
  }

  $failed = @($steps | Where-Object { -not [bool]$_.ok })
  if ($nextOcr -and -not [bool]$nextOcr.ok) {
    $failed += @([ordered]@{ step = "next_ocr97"; ok = $false })
  }
  $finalStatus = if ($failed.Count -eq 0) { "complete" } else { "complete_with_failures" }
  $apolloPayload = @{
    status = $finalStatus
    state = if ($failed.Count -eq 0) { "complete" } else { "failed" }
    run_id = $RunId
    ocr_task = $OcrTaskName
    ocr = $ocr
    steps = $steps
    failed_steps = @($failed | ForEach-Object { $_.step })
    step_summary = @($steps | ForEach-Object { "$($_.step):$(if ($_.ok) { 'ok' } else { 'failed' })" })
    latest_phase2 = (Join-Path $repoRoot "Apollo\logs\homework_trade_phase2\latest_homework_phase2.json")
    latest_practice = (Join-Path $repoRoot "Apollo\logs\homework_trade\latest_homework_trade.json")
    latest_swing_study = (Join-Path $repoRoot "Apollo\logs\swing_study\latest_swing_study.json")
    hippograph = (Join-Path $repoRoot "Apollo\chroma_db\financial_kg.json")
    status_path = $statusPath
    next_ocr = $nextOcr
    live_trade_execution = $false
    human_approval_required = $true
    message = "Apollo post-OCR chain: phase2 HippoRAG, swing study, HippoGraph build, nightly pipeline, mock swing prep$(if ($nextOcr) { ', next OCR97' } else { '' })."
  }
  Write-ChainStatus $apolloPayload
  Write-SkyHandoff -ApolloPayload $apolloPayload -OcrPayload @{
    ok = ([int]$ocr.last_task_result -eq 0)
    status = $ocrStatus
    task = $OcrTaskName
    last_result = [string]$ocr.last_task_result
    completed_at = ([datetime]$ocr.last_run_time).ToUniversalTime().ToString("o")
  } -NextOcrPayload $nextOcr
  if ($failed.Count -gt 0) {
    Send-ArgusAlert -Level "warn" -Title "Apollo night chain completed with failures" -Message "$RunId failed steps: $(@($failed | ForEach-Object { $_.step }) -join ', ')" -Event "apollo_night_chain_partial_failure"
    exit 1
  }
  Send-ArgusAlert -Level "info" -Title "Apollo night chain complete" -Message "$RunId finished after OCR task $OcrTaskName." -Event "apollo_night_chain_complete"
  exit 0
} catch {
  Write-ChainStatus @{
    status = "failed"
    run_id = $RunId
    reason = $_.Exception.Message
    ocr_task = $OcrTaskName
    ocr = $ocr
    live_trade_execution = $false
  }
  Send-ArgusAlert -Level "warn" -Title "Apollo night chain failed" -Message "$RunId blocked: $($_.Exception.Message)" -Event "apollo_night_chain_failed"
  exit 1
} finally {
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}
