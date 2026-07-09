param(
  [string]$TaskName = "ApolloPipelinePassApr28_0100",
  [string]$RunDate = "04/28/2026",
  [string]$RunTime = "01:00"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$runner = Join-Path $repoRoot "Apollo\watchdog\run_apollo_pipeline_pass_apr28_0100.ps1"
$powershellExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path $runner)) {
  throw "runner_not_found:$runner"
}

$taskAction = "`"$powershellExe`" -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$runner`""

& schtasks /Create /TN $TaskName /SC ONCE /SD $RunDate /ST $RunTime /TR $taskAction /F | Out-Host
& schtasks /Query /TN $TaskName /FO LIST /V | Out-Host

$manifestDir = Join-Path $repoRoot "_tmp\apollo_pipeline_pass_apr28_0100"
New-Item -ItemType Directory -Force -Path $manifestDir | Out-Null
$manifest = [ordered]@{
  task_name = $TaskName
  run_date_local = $RunDate
  run_time_local = $RunTime
  timezone = "America/Chicago"
  runner = $runner
  run_id = "apollo_pipeline_pass_apr28_0100"
  run_mode = "nightly"
  expected_run_dir = (Join-Path $repoRoot "Apollo\logs\nightly\2026-04-28\apollo_pipeline_pass_apr28_0100")
  output_dir = $manifestDir
  condition = "Run the legit Apollo pipeline pass only if ApolloPipelineTestApr27_0100 finishes with ok=true and quality.gate_pass=true. Otherwise write REMINDER.md and pass_status.json for Sky morning report."
  live_trade_execution = $false
  reporting = @{
    sky_morning_pipeline_task = "SkyMorningPipeline0810"
    sky_morning_pipeline_time_local = "08:10"
    sky_morning_report_includes_status = $true
  }
  resource_posture = @{
    below_normal_process_priority = $true
    OMP_NUM_THREADS = "4"
    OPENBLAS_NUM_THREADS = "4"
    MKL_NUM_THREADS = "4"
    NUMEXPR_NUM_THREADS = "4"
    OPENCV_FOR_THREADS_NUM = "4"
  }
  config = @{
    max_articles = 4
    hipporag_use_llm = $true
    focus_universe_decision_min_volume = 60
    gather_allow_seed_fallback = $false
    ocr_route_mode = "quality_first"
    ocr_require_finance_structure = $true
    ocr_stage_min_score = 85
  }
  created_at = (Get-Date).ToUniversalTime().ToString("o")
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $manifestDir "schedule_manifest.json") -Encoding UTF8
