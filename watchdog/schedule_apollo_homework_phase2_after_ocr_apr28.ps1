param(
  [string]$TaskName = "ApolloHomeworkPhase2AfterOCRApr28",
  [string]$RunDate = "",
  [string]$RunTime = "",
  [string]$Tickers = "NVDA,AMD,AVGO,MSFT,AMZN"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$runner = Join-Path $scriptDir "run_apollo_homework_phase2_after_ocr_apr28_scheduled.ps1"
if (-not (Test-Path $runner)) {
  throw "runner_missing:$runner"
}

if ([string]::IsNullOrWhiteSpace($RunDate) -or [string]::IsNullOrWhiteSpace($RunTime)) {
  $queueAt = (Get-Date).AddMinutes(1)
  $RunDate = $queueAt.ToString("MM/dd/yyyy")
  $RunTime = $queueAt.ToString("HH:mm")
} else {
  $queueAt = [datetime]::Parse("$RunDate $RunTime")
}

$powerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$arg = "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$action = New-ScheduledTaskAction -Execute $powerShellExe -Argument $arg -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Once -At $queueAt
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 72)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

$manifestDir = Join-Path $repoRoot "_tmp\apollo_homework_phase2_after_ocr_apr28"
New-Item -ItemType Directory -Force -Path $manifestDir | Out-Null
@{
  task_name = $TaskName
  run_date = $RunDate
  run_time = $RunTime
  timezone = "America/Chicago"
  runner = $runner
  upstream_ocr_task = "OCR97MixedCorpusBenchmarkApr28_0100"
  tickers = $Tickers
  phase = "strategy_kb_plus_hipporag_plus_homework_trade"
  strategy_kb_docs = "Apollo\memory\kb\ingest\swing_strategy"
  expected_latest = "Apollo\logs\homework_trade_phase2\latest_homework_phase2.json"
  live_trade_execution = $false
  human_approval_required = $true
} | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $manifestDir "schedule_manifest.json") -Encoding UTF8

schtasks /Query /TN $TaskName /FO LIST /V
