param(
    [string]$EventId = "",
    [string]$TaskName = ""
)

$ErrorActionPreference = "Continue"
Set-Location "C:\Users\blyth\Desktop\Engineering\Apollo"

$runStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$reportRoot = "C:\Users\blyth\Desktop\Engineering\Apollo\reports\ocr97_real_docs"
$logRoot = Join-Path $reportRoot "scheduled_logs"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

$stdoutLog = Join-Path $logRoot "ocr97_real_doc_benchmark_${runStamp}.out.log"
$stderrLog = Join-Path $logRoot "ocr97_real_doc_benchmark_${runStamp}.err.log"
$failureReport = Join-Path $reportRoot "scheduled_failure_${runStamp}.md"

$env:APOLLO_GB10_QWEN_OLLAMA_URL = if ($env:APOLLO_GB10_QWEN_OLLAMA_URL) { $env:APOLLO_GB10_QWEN_OLLAMA_URL } else { "http://127.0.0.1:11434" }
$env:APOLLO_GB10_QWEN_OCR_MODEL = if ($env:APOLLO_GB10_QWEN_OCR_MODEL) { $env:APOLLO_GB10_QWEN_OCR_MODEL } else { "qwen3-vl:32b" }
$env:APOLLO_GB10_QWEN_OCR_FALLBACK_MODEL = if ($env:APOLLO_GB10_QWEN_OCR_FALLBACK_MODEL) { $env:APOLLO_GB10_QWEN_OCR_FALLBACK_MODEL } else { "qwen3-vl:32b" }
$env:APOLLO_GB10_OCR_TIMEOUT_SEC = if ($env:APOLLO_GB10_OCR_TIMEOUT_SEC) { $env:APOLLO_GB10_OCR_TIMEOUT_SEC } else { "300" }
$env:VISION_OLLAMA_URL = if ($env:VISION_OLLAMA_URL) { $env:VISION_OLLAMA_URL } else { "http://127.0.0.1:11434" }
$env:VISION_QWEN3VL_MODEL = if ($env:VISION_QWEN3VL_MODEL) { $env:VISION_QWEN3VL_MODEL } else { "qwen3-vl:32b" }

$selectedManifest = python "tools\select_ocr97_nightly_manifest.py"
if (-not $selectedManifest -or -not (Test-Path -LiteralPath $selectedManifest)) {
    $selectedManifest = "config\ocr97_real_documents_manifest.json"
}
Write-Output "[ocr97_scheduler] manifest: $selectedManifest" | Out-File -Append $stdoutLog
python "tools\run_ocr97_real_doc_benchmark.py" --manifest "$selectedManifest" --update-readme > $stdoutLog 2> $stderrLog
$exitCode = $LASTEXITCODE

$skyHandoffPath = "C:\Users\blyth\Desktop\Engineering\Sky\logs\overnight_run_status.json"
$completedAt = (Get-Date).ToUniversalTime().ToString("o")
New-Item -ItemType Directory -Force -Path (Split-Path $skyHandoffPath) | Out-Null
@{
    date       = (Get-Date).ToString("yyyy-MM-dd")
    updated_at = $completedAt
    ocr97      = @{
        ok           = ($exitCode -eq 0)
        status       = if ($exitCode -eq 0) { "complete" } else { "failed" }
        exit_code    = $exitCode
        completed_at = $completedAt
        stdout_log   = $stdoutLog
        stderr_log   = $stderrLog
        task_name    = $TaskName
        event_id     = $EventId
    }
} | ConvertTo-Json -Depth 6 | Set-Content -Path $skyHandoffPath -Encoding UTF8

if ($exitCode -eq 0) {
    python "tools\ocr97_real_doc_calendar_update.py" --event-id "$EventId" --exit-code $exitCode --task-name "$TaskName" --stdout-log "$stdoutLog" >> $stdoutLog 2>> $stderrLog
}

$ftpTrackerLog = Join-Path $logRoot "ftp_tracker_${runStamp}.log"
python "C:\Users\blyth\Desktop\Engineering\Aegis\tools\ocr97_realdoc_ftp_tracker.py" --stdout-log "$stdoutLog" --manifest "$selectedManifest" --task-name "$TaskName" --exit-code $exitCode | Out-File -Encoding UTF8 -FilePath $ftpTrackerLog

if ($exitCode -ne 0) {
    $startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
    $stdoutTail = ""
    $stderrTail = ""
    if (Test-Path $stdoutLog) {
        $stdoutTail = (Get-Content -Path $stdoutLog -Tail 80 -ErrorAction SilentlyContinue) -join "`n"
    }
    if (Test-Path $stderrLog) {
        $stderrTail = (Get-Content -Path $stderrLog -Tail 80 -ErrorAction SilentlyContinue) -join "`n"
    }
    @"
# OCR97 Real Document Benchmark Scheduled Failure

- Task: $TaskName
- Event: $EventId
- Exit code: $exitCode
- Recorded at: $startedAt
- Stdout log: $stdoutLog
- Stderr log: $stderrLog

## Stdout Tail

``````
$stdoutTail
``````

## Stderr Tail

``````
$stderrTail
``````
"@ | Set-Content -Path $failureReport -Encoding UTF8

    python "tools\ocr97_real_doc_failure_fallback.py" --event-id "$EventId" --exit-code $exitCode --task-name "$TaskName"
}
exit $exitCode
