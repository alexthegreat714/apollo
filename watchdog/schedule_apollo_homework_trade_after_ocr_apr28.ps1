param(
  [string]$TaskName = "ApolloHomeworkTradeAfterOCRApr28",
  [string]$RunDate = "04/28/2026",
  [string]$RunTime = "06:15",
  [string]$Tickers = "NVDA,AMD,AVGO,MSFT,AMZN"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptDir "run_apollo_homework_trade_after_ocr_apr28_scheduled.ps1"
if (-not (Test-Path $runner)) {
  throw "runner_missing:$runner"
}

$arg = "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$runner`""
schtasks /Create /TN $TaskName /SC ONCE /SD $RunDate /ST $RunTime /TR "powershell.exe $arg" /F | Out-Null

$manifestDir = Join-Path (Resolve-Path (Join-Path $scriptDir "..\..")) "_tmp\apollo_homework_trade_after_ocr_apr28"
New-Item -ItemType Directory -Force -Path $manifestDir | Out-Null
@{
  task_name = $TaskName
  run_date = $RunDate
  run_time = $RunTime
  timezone = "America/Chicago"
  runner = $runner
  upstream_ocr_task = "OCR97MixedCorpusBenchmarkApr28_0100"
  tickers = $Tickers
  live_trade_execution = $false
  human_approval_required = $true
  expected_latest = "Apollo\logs\homework_trade\latest_homework_trade.json"
} | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $manifestDir "schedule_manifest.json") -Encoding UTF8

schtasks /Query /TN $TaskName /FO LIST /V
