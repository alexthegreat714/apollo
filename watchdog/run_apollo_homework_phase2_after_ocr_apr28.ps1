param(
  [string]$OcrTaskName = "OCR97MixedCorpusBenchmarkApr28_0100",
  [string]$Tickers = "NVDA,AMD,AVGO,MSFT,AMZN",
  [int]$MaxWaitMinutes = 480,
  [int]$PollSeconds = 60,
  [int]$GraphMaxNewChunks = 1000,
  [int]$GraphTimeoutSeconds = 1800,
  [switch]$Notify
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$logDir = Join-Path $repoRoot "Apollo\logs\homework_trade_phase2\scheduler"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$lockPath = Join-Path $logDir "apollo_homework_phase2_after_ocr.lock"

function Write-Status {
  param([hashtable]$Payload)
  $Payload["updated_at"] = (Get-Date).ToUniversalTime().ToString("o")
  $Payload | ConvertTo-Json -Depth 10 | Set-Content -Path (Join-Path $logDir "apollo_homework_phase2_after_ocr_status.json") -Encoding UTF8
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

function Send-ArgusAlert {
  param(
    [string]$Level,
    [string]$Title,
    [string]$Message,
    [string]$Event
  )
  $payload = @{
    level = $Level
    title = $Title
    message = $Message
    tags = @("apollo", "homework", "strategy")
    source = "apollo"
    event = $Event
  } | ConvertTo-Json -Depth 6
  try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5216/alerts/external" -ContentType "application/json" -Body $payload | Out-Null
  } catch {
  }
}

if (Test-Path $lockPath) {
  $lockAge = (Get-Date) - (Get-Item $lockPath).LastWriteTime
  if ($lockAge.TotalHours -lt 10) {
    Write-Status @{
      status = "skipped_active_lock"
      lock_path = $lockPath
      lock_age_minutes = [math]::Round($lockAge.TotalMinutes, 1)
      live_trade_execution = $false
    }
    exit 0
  }
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

"pid=$PID started=$(Get-Date -Format o)" | Set-Content -Path $lockPath -Encoding UTF8

try {
  [System.Diagnostics.Process]::GetCurrentProcess().PriorityClass = [System.Diagnostics.ProcessPriorityClass]::BelowNormal
} catch {
}

$deadline = (Get-Date).AddMinutes($MaxWaitMinutes)
$ocr = Get-TaskSnapshot -Name $OcrTaskName
$status = "waiting_for_ocr"
Write-Status @{
  status = $status
  ocr_task = $OcrTaskName
  ocr = $ocr
  live_trade_execution = $false
}

try {
  while ((Get-Date) -lt $deadline) {
    $ocr = Get-TaskSnapshot -Name $OcrTaskName
    if (-not [bool]$ocr.found) {
      throw "ocr_task_missing:$OcrTaskName"
    }

    if ([string]$ocr.state -eq "Running") {
      Write-Status @{
        status = "waiting_for_ocr"
        ocr_task = $OcrTaskName
        ocr = $ocr
        live_trade_execution = $false
      }
      Start-Sleep -Seconds $PollSeconds
      continue
    }

    if ($ocr.last_run_time -and ([datetime]$ocr.last_run_time).Year -ge 2026) {
      $status = if ([int]$ocr.last_task_result -eq 0) { "ocr_complete" } else { "ocr_complete_nonzero" }
      break
    }

    Start-Sleep -Seconds $PollSeconds
  }

  if ($status -eq "waiting_for_ocr") {
    throw "ocr_wait_timeout_after_${MaxWaitMinutes}_minutes"
  }

  $pythonExe = "python"
  $candidate = "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe"
  if (Test-Path $candidate) {
    $pythonExe = $candidate
  }

  Set-Location $repoRoot
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $stdout = Join-Path $logDir "apollo_homework_phase2_$stamp.stdout.log"
  $stderr = Join-Path $logDir "apollo_homework_phase2_$stamp.stderr.log"
  $argsList = @(
    "-m", "Apollo.homework_phase2_pipeline",
    "run",
    "--tickers", $Tickers,
    "--upstream-ocr-task", $OcrTaskName,
    "--upstream-ocr-status", $status,
    "--upstream-ocr-result", ([string]$ocr.last_task_result),
    "--upstream-ocr-completed-at", ([datetime]$ocr.last_run_time).ToUniversalTime().ToString("o"),
    "--graph-max-new-chunks", ([string]$GraphMaxNewChunks),
    "--graph-timeout-sec", ([string]$GraphTimeoutSeconds)
  )
  if ($Notify) {
    $argsList += "--notify"
  }

  Write-Status @{
    status = "running_homework_phase2"
    ocr_task = $OcrTaskName
    ocr = $ocr
    stdout = $stdout
    stderr = $stderr
    tickers = $Tickers
    graph_max_new_chunks = $GraphMaxNewChunks
    live_trade_execution = $false
  }

  $proc = Start-Process -FilePath $pythonExe -ArgumentList $argsList -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  if ($proc.ExitCode -ne 0) {
    Write-Status @{
      status = "failed"
      reason = "homework_phase2_exit_$($proc.ExitCode)"
      ocr_task = $OcrTaskName
      ocr = $ocr
      stdout = $stdout
      stderr = $stderr
      live_trade_execution = $false
    }
    Send-ArgusAlert -Level "warn" -Title "Apollo homework phase 2 failed" -Message "Strategy-aware homework exited $($proc.ExitCode). stderr: $stderr" -Event "apollo_homework_phase2_failed"
    exit $proc.ExitCode
  }

  Write-Status @{
    status = "complete"
    reason = "homework_phase2_process_finished"
    ocr_task = $OcrTaskName
    ocr = $ocr
    stdout = $stdout
    stderr = $stderr
    latest_path = (Join-Path $repoRoot "Apollo\logs\homework_trade_phase2\latest_homework_phase2.json")
    live_trade_execution = $false
  }
} catch {
  Write-Status @{
    status = "failed"
    reason = $_.Exception.Message
    ocr_task = $OcrTaskName
    ocr = $ocr
    live_trade_execution = $false
  }
  Send-ArgusAlert -Level "warn" -Title "Apollo homework phase 2 blocked" -Message $_.Exception.Message -Event "apollo_homework_phase2_blocked"
  exit 1
} finally {
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

exit 0
