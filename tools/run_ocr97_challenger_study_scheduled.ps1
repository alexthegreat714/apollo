param(
    [string]$EventId = "",
    [string]$TaskName = ""
)

$ErrorActionPreference = "Continue"
Set-Location "C:\Users\blyth\Desktop\Engineering\Apollo"

$runStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$reportRoot = "C:\Users\blyth\Desktop\Engineering\Apollo\reports\ocr97_challenger_study"
$logRoot = Join-Path $reportRoot "scheduled_logs"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

$stdoutLog = Join-Path $logRoot "ocr97_challenger_study_${runStamp}.out.log"
$stderrLog = Join-Path $logRoot "ocr97_challenger_study_${runStamp}.err.log"

python "tools\run_ocr97_challenger_study.py" --run-id "ocr97_challenger_$runStamp" > $stdoutLog 2> $stderrLog
$exitCode = $LASTEXITCODE

$skyHandoffPath = "C:\Users\blyth\Desktop\Engineering\Sky\logs\overnight_run_status.json"
$completedAt = (Get-Date).ToUniversalTime().ToString("o")
$latestIndexPath = Join-Path $reportRoot "latest_index.json"
$latestReportPath = Join-Path $reportRoot "latest_report.md"
New-Item -ItemType Directory -Force -Path (Split-Path $skyHandoffPath) | Out-Null

$current = @{}
if (Test-Path $skyHandoffPath) {
    try {
        $current = Get-Content $skyHandoffPath -Raw | ConvertFrom-Json -AsHashtable
    } catch {
        $current = @{}
    }
}
if (-not $current) { $current = @{} }
$current["date"] = (Get-Date).ToString("yyyy-MM-dd")
$current["updated_at"] = $completedAt
$current["ocr97_challenger_study"] = @{
    ok = ($exitCode -eq 0)
    status = if ($exitCode -eq 0) { "complete" } else { "failed" }
    exit_code = $exitCode
    completed_at = $completedAt
    stdout_log = $stdoutLog
    stderr_log = $stderrLog
    task_name = $TaskName
    event_id = $EventId
    latest_index_path = $latestIndexPath
    latest_report_path = $latestReportPath
}
$current | ConvertTo-Json -Depth 8 | Set-Content -Path $skyHandoffPath -Encoding UTF8

exit $exitCode
