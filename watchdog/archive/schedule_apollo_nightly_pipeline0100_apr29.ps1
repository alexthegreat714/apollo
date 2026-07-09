param(
  [string]$TaskName = "ApolloNightlyPipeline0100",
  [string]$RunDate = "04/29/2026",
  [string]$RunTime = "01:00"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$runner = Join-Path $repoRoot "Apollo\watchdog\run_apollo_nightly_pipeline0100_apr29_guarded.ps1"
$powershellExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path $runner)) {
  throw "runner_not_found:$runner"
}

$taskAction = "`"$powershellExe`" -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$runner`""

& schtasks /Create /TN $TaskName /SC ONCE /SD $RunDate /ST $RunTime /TR $taskAction /F | Out-Host
& schtasks /Change /TN $TaskName /Enable | Out-Host
& schtasks /Query /TN $TaskName /FO LIST /V | Out-Host

$manifestDir = Join-Path $repoRoot "_tmp\apollo_nightly_pipeline0100_apr29"
New-Item -ItemType Directory -Force -Path $manifestDir | Out-Null
$manifest = [ordered]@{
  task_name = $TaskName
  run_date_local = $RunDate
  run_time_local = $RunTime
  timezone = "America/Chicago"
  runner = $runner
  run_id = "apollo_nightly_pipeline0100_apr29"
  run_mode = "nightly"
  expected_run_dir = (Join-Path $repoRoot "Apollo\logs\nightly\2026-04-29\apollo_nightly_pipeline0100_apr29")
  output_dir = $manifestDir
  condition = "Update and run ApolloNightlyPipeline0100 only after ApolloPipelinePassApr28_0100 finishes with ok=true and quality.gate_pass=true. Otherwise write REMINDER.md and promotion_status.json for Sky morning report."
  live_trade_execution = $false
  reporting = @{
    sky_morning_pipeline_task = "SkyMorningPipeline0810"
    sky_morning_pipeline_time_local = "08:10"
    sky_morning_report_includes_status = $true
  }
  created_at = (Get-Date).ToUniversalTime().ToString("o")
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $manifestDir "schedule_manifest.json") -Encoding UTF8
