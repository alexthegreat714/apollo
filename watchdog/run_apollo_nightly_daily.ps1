param()
$ErrorActionPreference = "Stop"

$repoRoot = "C:\Users\blyth\Desktop\Engineering"
$pythonExe = "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe"
$logDir = Join-Path $repoRoot "Apollo\logs\nightly\scheduler_runs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdout = Join-Path $logDir "nightly_${stamp}.stdout.log"
$stderr = Join-Path $logDir "nightly_${stamp}.stderr.log"

$env:OMP_NUM_THREADS = "4"
$env:OPENBLAS_NUM_THREADS = "4"
$env:MKL_NUM_THREADS = "4"
$env:NUMEXPR_NUM_THREADS = "4"
$env:APOLLO_FAST_LLM_BASE_URL = if ($env:APOLLO_FAST_LLM_BASE_URL) { $env:APOLLO_FAST_LLM_BASE_URL } else { "http://127.0.0.1:11434" }
$env:APOLLO_FAST_LLM_MODEL = if ($env:APOLLO_FAST_LLM_MODEL) { $env:APOLLO_FAST_LLM_MODEL } else { "gemma3:12b" }
$env:APOLLO_TRADE_REASON_LLM_BASE_URL = if ($env:APOLLO_TRADE_REASON_LLM_BASE_URL) { $env:APOLLO_TRADE_REASON_LLM_BASE_URL } else { "http://127.0.0.1:11434" }
$env:APOLLO_TRADE_REASON_LLM_MODEL = if ($env:APOLLO_TRADE_REASON_LLM_MODEL) { $env:APOLLO_TRADE_REASON_LLM_MODEL } else { "qwen2.5:72b" }
$env:APOLLO_TRADE_REASON_LLM_FALLBACKS = if ($env:APOLLO_TRADE_REASON_LLM_FALLBACKS) { $env:APOLLO_TRADE_REASON_LLM_FALLBACKS } else { "gemma-3-27b-it-Q4_K_M:latest,gemma-4-26b-a4b-q4km-ctx8:latest,gemma3:12b" }
$env:APOLLO_MODEL_STORAGE_ROOT = if ($env:APOLLO_MODEL_STORAGE_ROOT) { $env:APOLLO_MODEL_STORAGE_ROOT } else { "D:\ApolloModels" }
$env:APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS = if ($env:APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS) { $env:APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS } else { "0" }
$env:OLLAMA_MODELS = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:APOLLO_MODEL_STORAGE_ROOT "ollama" }
$env:HF_HOME = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:APOLLO_MODEL_STORAGE_ROOT "huggingface" }
$env:TRANSFORMERS_CACHE = if ($env:TRANSFORMERS_CACHE) { $env:TRANSFORMERS_CACHE } else { Join-Path $env:HF_HOME "transformers" }
$env:TORCH_HOME = if ($env:TORCH_HOME) { $env:TORCH_HOME } else { Join-Path $env:APOLLO_MODEL_STORAGE_ROOT "torch" }
$env:APOLLO_SIM_MAIN_LANE = "aggressive_news_cycle"
$env:APOLLO_SIM_BROKER_PROVIDER = "paper_sim"
$env:APOLLO_SIM_EXECUTION_MODE = "paper"
$env:APOLLO_SIM_LIVE_ENABLED = "0"
$hipporagModel = if ($env:APOLLO_HIPPORAG_LLM_MODEL) { $env:APOLLO_HIPPORAG_LLM_MODEL } else { $env:APOLLO_FAST_LLM_MODEL }
# This switch controls LLM triple extraction during the 1 AM corpus build only.
# Trade decisions still use the HippoRAG graph, and the bounded 3:15 LLM refresh remains scheduled.
$nightlyHipporagUseLlm = if ($env:APOLLO_NIGHTLY_HIPPORAG_USE_LLM) { @("1", "true", "yes", "on") -contains $env:APOLLO_NIGHTLY_HIPPORAG_USE_LLM.ToLowerInvariant() } else { $false }

$payloadJson = @{
    run_mode                           = "nightly"
    batch_id                           = "daily_nightly_${stamp}"
    topic                              = "one discretionary stock trade decision per day with overnight holding allowed"
    max_articles                       = 6
    equity_news_tickers                = @("NVDA","AMD","AVGO","TSM","MSFT","AMZN","GOOGL","META","JPM","GS","BAC","XOM","CVX","LLY","UNH","JNJ","CAT","HON")
    hipporag_use_llm                   = $nightlyHipporagUseLlm
    hipporag_llm_model                 = $hipporagModel
    allow_cpu_fallback                 = $false
    # Match the more resilient trade-cycle gather posture for the scheduled nightly lane.
    focus_universe_enabled             = $false
    focus_universe_gb10_only           = $true
    focus_universe_decision_min_volume = 60
    allow_inbox                        = $false
    gather_allow_seed_fallback         = $true
    gather_web_rate_limit_retries      = 1
    ocr_route_mode                     = "quality_first"
    memory_guard_enabled               = $true
    memory_min_available_mb            = 4096
    memory_max_used_percent            = 92
    memory_max_vmmem_mb                = 12288
    memory_max_ollama_runners          = 4
    preflight_max_wait_sec             = 1800
    overall_timeout_sec                = 28800
} | ConvertTo-Json -Depth 8

$payloadPath = Join-Path $logDir "payload_${stamp}.json"
$payloadJson | Set-Content -Path $payloadPath -Encoding UTF8

Set-Location $repoRoot
$nightlyExitCode = 1
try {
    & $pythonExe -m Apollo.nightly_pipeline run --payload-file $payloadPath 1> $stdout 2> $stderr
    if ($null -ne $LASTEXITCODE) {
        $nightlyExitCode = [int]$LASTEXITCODE
    }
} catch {
    $nightlyExitCode = 1
    Add-Content -Path $stderr -Value ("nightly_wrapper_failed: " + $_.Exception.Message) -Encoding UTF8
}

$hippoWrapper = Join-Path $repoRoot "Apollo\watchdog\run_apollo_improved_hipporag_0315.ps1"
if (Test-Path $hippoWrapper) {
    $hippoStdout = Join-Path $logDir "post_nightly_hipporag_${stamp}.stdout.log"
    $hippoStderr = Join-Path $logDir "post_nightly_hipporag_${stamp}.stderr.log"
    & "C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe" -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File $hippoWrapper 1> $hippoStdout 2> $hippoStderr
    $hippoExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 1 }
    if ($hippoExitCode -ne 0) {
        Add-Content -Path $stderr -Value "Post-nightly HippoRAG refresh exited with code $hippoExitCode. See $hippoStderr" -Encoding UTF8
    }
}

# Self-perpetuate: schedule tomorrow night's curated enrichment event in Sky's calendar.
# Sky's calendar_run_watchdog will detect the PS1 path and execute via sky.local_command hook.
# This keeps the chain running after the 45-day seed window expires.
try {
    # 11 PM tonight - finishes before the 12:30 AM KG LLM task loads gemma3:12b
    $tonight11pm = (Get-Date).Date.AddHours(23)
    $tomorrow9am = $tonight11pm  # alias kept for variable reuse below
    $tomorrow9amEnd = $tonight11pm.AddHours(2)
    $tomorrowDate = $tonight11pm.ToString("yyyy-MM-dd")
    $ps1Path = "C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_curated_enrichment.ps1"

    # Check if an event for tomorrow already exists (e.g. from seed)
    $existingEvents = $null
    try {
        $existingEvents = (Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:5011/calendar/events" -TimeoutSec 8).events
    } catch {
        $existingEvents = @()
    }
    $alreadyExists = $false
    if ($existingEvents) {
        foreach ($ev in $existingEvents) {
            $evTitle = if ($ev.title) { [string]$ev.title } else { "" }
            $evStart = if ($ev.start) { [string]$ev.start } else { "" }
            if ($evTitle -like "*Curated KG Enrichment*" -and $evStart -like "*$tomorrowDate*") {
                $alreadyExists = $true
                break
            }
        }
    }

    if (-not $alreadyExists) {
        $eventPayload = @{
            title    = "Apollo Curated KG Enrichment (qwen3-vl:32b)"
            start    = $tonight11pm.ToString("o")
            end      = $tomorrow9amEnd.ToString("o")
            status   = "pending"
            source   = "apollo"
            category = "apollo"
            trigger  = "Scheduled curated HippoRAG enrichment for $tomorrowDate. Runs $ps1Path - 120 high-priority chunks, qwen3-vl:32b."
            notes    = "Curated KG enrichment: priority-scored chunks, qwen3-vl:32b on GX10, confidence>=7, dedup against existing edges.`nScript: $ps1Path"
            gb10_expected_percent = 85
        } | ConvertTo-Json -Depth 5

        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5011/calendar/events" `
            -ContentType "application/json" -Body $eventPayload -TimeoutSec 10 | Out-Null
        Add-Content -Path $stdout -Value "[nightly_chain] Posted curated enrichment event for $tomorrowDate 09:00" -Encoding UTF8
    } else {
        Add-Content -Path $stdout -Value "[nightly_chain] Curated enrichment event for $tomorrowDate already exists - skipped." -Encoding UTF8
    }
} catch {
    Add-Content -Path $stdout -Value "[nightly_chain] Could not post curated enrichment event: $($_)" -Encoding UTF8
}

exit $nightlyExitCode
