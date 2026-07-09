param(
    [datetime]$RunAt = [datetime]"2026-05-15 23:00:00",
    [string]$TaskName = "ApolloHomeworkTomorrowNight"
)
$ErrorActionPreference = "Stop"

$repoRoot = "C:\Users\blyth\Desktop\Engineering"
$psExe = "C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe"
$scriptPath = "C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_nightly_daily.ps1"

if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "missing_nightly_runner:$scriptPath"
}

$runAtText = $RunAt.ToString("MM/dd/yyyy HH:mm")
$taskArgs = "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$taskRun = "`"$psExe`" $taskArgs"

schtasks /Create /TN $TaskName /SC ONCE /SD $RunAt.ToString("MM/dd/yyyy") /ST $RunAt.ToString("HH:mm") /TR $taskRun /F | Out-Host

$recordDir = Join-Path $repoRoot "Apollo\logs\nightly\scheduler_runs"
New-Item -ItemType Directory -Force -Path $recordDir | Out-Null
$recordPath = Join-Path $recordDir "homework_tomorrow_night_task.json"
@{
    ok = $true
    task_name = $TaskName
    run_at_local = $runAtText
    timezone = "America/Chicago"
    command = $taskRun
    created_at_local = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    note = "One-shot Apollo nightly homework run. This schedules the pipeline only; it does not start it now."
} | ConvertTo-Json -Depth 5 | Set-Content -Path $recordPath -Encoding UTF8

Write-Host "Scheduled $TaskName for $runAtText CT"
Write-Host "Record: $recordPath"
schtasks /query /tn $TaskName /fo LIST /v | Select-String "TaskName|Task To Run|Next Run Time|Status"
