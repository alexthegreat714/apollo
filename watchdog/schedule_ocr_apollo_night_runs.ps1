param(
  [string[]]$Dates = @("2026-04-29", "2026-04-30", "2026-05-01"),
  [string]$Tickers = "NVDA,AMD,AVGO,MSFT,AMZN",
  [switch]$Register
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$preflightRunner = Join-Path $repoRoot "ocr97\tools\run_argus_ocr97_preflight_scheduled.ps1"
$ocrRunner = Join-Path $repoRoot "ocr97\tools\run_mixed_corpus_overnight_scheduled.ps1"
$chainRunner = Join-Path $repoRoot "Apollo\watchdog\run_apollo_night_chain_after_ocr.ps1"
$manifestDir = Join-Path $repoRoot "_ops\contracts"

foreach ($path in @($preflightRunner, $ocrRunner, $chainRunner)) {
  if (-not (Test-Path $path)) { throw "runner_missing:$path" }
}

function Register-OneTask {
  param(
    [string]$TaskName,
    [datetime]$At,
    [string]$Runner,
    [string]$Arguments,
    [int]$Hours = 12
  )
  $actionArg = "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$Runner`" $Arguments"
  $action = New-ScheduledTaskAction -Execute $powerShellExe -Argument $actionArg -WorkingDirectory $repoRoot
  $trigger = New-ScheduledTaskTrigger -Once -At $At
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours $Hours) -MultipleInstances IgnoreNew -StartWhenAvailable
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
}

$planned = @()
foreach ($dateText in $Dates) {
  $date = [datetime]::ParseExact($dateText, "yyyy-MM-dd", $null)
  $stamp = $date.ToString("yyyyMMdd")
  $runDir = ".\_tmp\ocr97_mixed_corpus_$stamp`_0100"
  $ocrTask = "OCR97MixedCorpusBenchmark$stamp`_0100"
  $preflightTask = "ArgusOCR97MixedCorpusPreflight$stamp`_0050"
  $chainTask = "ApolloNightChainAfterOCR$stamp`_0105"
  $runId = "apollo_night_chain_$stamp"

  $preflightAt = $date.Date.AddMinutes(50)
  $ocrAt = $date.Date.AddHours(1)
  $chainAt = $date.Date.AddHours(1).AddMinutes(5)

  $preflightArgs = "-RunDir `"$runDir`" -Instance `"ocr97_mixed_corpus_$stamp`_preflight_0050`" -WarnCpuPct 85 -BlockCpuPct 97 -SampleSeconds 5 -MaxWaitMinutes 10"
  $ocrArgs = "-RunDir `"$runDir`" -RunId `"ocr97_mixed_corpus_$stamp`_0100`" -BroadLimit 0 -FocusLimit 20"
  $chainArgs = "-OcrTaskName `"$ocrTask`" -RunId `"$runId`" -RunDate `"$dateText`" -Tickers `"$Tickers`" -MaxWaitMinutes 540 -PollSeconds 60 -GraphMaxNewChunks 1200 -GraphTimeoutSeconds 2400 -Notify"

  if ($Register) {
    Register-OneTask -TaskName $preflightTask -At $preflightAt -Runner $preflightRunner -Arguments $preflightArgs -Hours 1
    Register-OneTask -TaskName $ocrTask -At $ocrAt -Runner $ocrRunner -Arguments $ocrArgs -Hours 10
    Register-OneTask -TaskName $chainTask -At $chainAt -Runner $chainRunner -Arguments $chainArgs -Hours 14
  }

  $planned += [ordered]@{
    date = $dateText
    timezone = "America/Chicago"
    preflight = @{ task_name = $preflightTask; starts = $preflightAt.ToString("s"); runner = $preflightRunner }
    ocr = @{ task_name = $ocrTask; starts = $ocrAt.ToString("s"); runner = $ocrRunner; run_dir = $runDir }
    apollo_chain = @{
      task_name = $chainTask
      starts = $chainAt.ToString("s")
      waits_for = $ocrTask
      runner = $chainRunner
      run_id = $runId
      steps = @(
        "homework_phase2_strategy_kb_hipporag",
        "swing_trade_study",
        "hippograph_build",
        "apollo_nightly_pipeline",
        "mock_swing_trade_prep"
      )
    }
    live_trade_execution = $false
    human_approval_required = $true
  }
}

New-Item -ItemType Directory -Force -Path $manifestDir | Out-Null
$manifest = [ordered]@{
  contract_id = "ocr_apollo_night_runs_20260429_20260501"
  created_at = (Get-Date).ToUniversalTime().ToString("o")
  registered = [bool]$Register
  objective = "Schedule OCR97 mixed-corpus runs followed by Apollo HippoRAG, nightly pipeline, and practice trade research for the next few nights."
  cadence = "one_time_tasks_for_each_night"
  sparse_notifications = @("chain complete", "chain failure", "phase2/trade high-confidence events emitted by Apollo")
  runs = $planned
}
$manifestPath = Join-Path $manifestDir "ocr_apollo_night_runs_20260429_20260501.json"
$manifest | ConvertTo-Json -Depth 12 | Set-Content -Path $manifestPath -Encoding UTF8

foreach ($item in $planned) {
  $stamp = ([datetime]::ParseExact([string]$item.date, "yyyy-MM-dd", $null)).ToString("yyyyMMdd")
  $chainStatusPath = Join-Path $repoRoot "Apollo\logs\night_chain\$($item.date)\$($item.apollo_chain.run_id)\status.json"
  $chainTracePath = Join-Path $repoRoot "Apollo\logs\night_chain\$($item.date)\$($item.apollo_chain.run_id)"
  $contractPath = Join-Path $manifestDir "apollo_night_chain_$stamp.json"
  [ordered]@{
    contract_id = "apollo_night_chain_$stamp"
    title = "Apollo post-OCR swing/HippoGraph chain $($item.date)"
    owner = "Aegis"
    systems = @("Apollo", "OCR97", "Sky")
    objective = "After OCR97 completes, run Apollo Phase 2 HippoRAG, explicit swing trade study, HippoGraph build, nightly pipeline, and mock swing trade prep. No live trade execution."
    schedule = @{
      start = ([datetime]$item.apollo_chain.starts).ToUniversalTime().ToString("o")
      end = ([datetime]$item.apollo_chain.starts).AddHours(12).ToUniversalTime().ToString("o")
      timezone = "America/Chicago"
    }
    artifacts = @{
      status_path = $chainStatusPath
      evidence_path = $chainStatusPath
      trace_path = $chainTracePath
    }
    reporting = @{
      calendar_title = "Apollo post-OCR swing/HippoGraph chain"
      morning_summary = "Reports OCR handoff, Phase 2/HippoRAG, explicit swing study, HippoGraph build, nightly pipeline, and mock swing trade prep results."
      next_decision = "Review failed_steps, latest_swing_study, latest_practice, and financial_kg.json before relying on outputs."
      reminder_text = "No live trade execution; human approval required for real orders."
    }
  } | ConvertTo-Json -Depth 12 | Set-Content -Path $contractPath -Encoding UTF8
}

if ($Register) {
  foreach ($item in $planned) {
    schtasks /Query /TN $item.preflight.task_name /FO LIST /V | Out-Null
    schtasks /Query /TN $item.ocr.task_name /FO LIST /V | Out-Null
    schtasks /Query /TN $item.apollo_chain.task_name /FO LIST /V | Out-Null
  }
}

Write-Output $manifestPath
