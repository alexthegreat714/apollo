param(
  [switch]$NoReset
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$runId = "apollo_pipeline_test_apr27_0100"
$runDate = "2026-04-27"
$runDir = Join-Path $repoRoot "Apollo\logs\nightly\$runDate\$runId"
$manifestDir = Join-Path $repoRoot "_tmp\apollo_pipeline_test_apr27_0100"
$logDir = Join-Path $manifestDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if ((-not $NoReset) -and (Test-Path $runDir)) {
  Remove-Item -Path $runDir -Recurse -Force
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

$seedDir = Join-Path $repoRoot "Apollo\logs\homework_seed_docs"
$payload = [ordered]@{
  run_mode = "quality_sample"
  batch_id = $runId
  topic = "one discretionary stock trade decision per day with overnight holding allowed"
  allow_web = $false
  allow_inbox = $false
  sources = @(
    (Join-Path $seedDir "finra_margin_risk_playbook.pdf"),
    (Join-Path $seedDir "sec_margin_execution_study.pdf"),
    (Join-Path $seedDir "federal_reserve_microstructure_notes.pdf"),
    (Join-Path $seedDir "nber_trading_behavioral_controls.pdf")
  )
  hipporag_use_llm = $true
  allow_cpu_fallback = $false
  focus_universe_enabled = $true
  focus_universe_gb10_only = $true
  focus_universe_decision_min_volume = 100
  strict_stage_min_score = 70
  gather_allow_seed_fallback = $false
  gather_min_accepted_sources = 3
  gather_min_avg_score = 50
  gather_min_trusted_ratio = 0.5
  gather_min_doc_sources = 2
  gather_min_pdf_sources = 2
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
$stdout = Join-Path $logDir "apollo_pipeline_test_$stamp.stdout.log"
$stderr = Join-Path $logDir "apollo_pipeline_test_$stamp.stderr.log"

$argsList = @(
  "-m", "Apollo.nightly_pipeline",
  "run",
  "--payload-file", $payloadFile
)

$proc = Start-Process -FilePath $pythonExe -ArgumentList $argsList -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
if ($proc.ExitCode -ne 0) {
  if (Test-Path $stderr) {
    Get-Content $stderr -Tail 80
  }
  exit $proc.ExitCode
}

exit 0
