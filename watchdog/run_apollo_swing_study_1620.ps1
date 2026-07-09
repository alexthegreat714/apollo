param(
  [string]$Tickers = "",
  [switch]$Notify
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$logDir = Join-Path $repoRoot "Apollo\logs\swing_study\scheduler"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$lockPath = Join-Path $logDir "apollo_swing_study.lock"

if (Test-Path $lockPath) {
  $lockAge = (Get-Date) - (Get-Item $lockPath).LastWriteTime
  if ($lockAge.TotalHours -lt 3) {
    $skipLog = Join-Path $logDir ("apollo_swing_study_skipped_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
    "Skipped: active lock exists at $lockPath age_minutes=$([math]::Round($lockAge.TotalMinutes, 1))" | Out-File -FilePath $skipLog -Encoding utf8
    exit 0
  }
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

"pid=$PID started=$(Get-Date -Format o)" | Out-File -FilePath $lockPath -Encoding utf8

try {
  [System.Diagnostics.Process]::GetCurrentProcess().PriorityClass = [System.Diagnostics.ProcessPriorityClass]::BelowNormal
} catch {
}

$pythonExe = "python"
$candidate = "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe"
if (Test-Path $candidate) {
  $pythonExe = $candidate
}

Set-Location $repoRoot
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdout = Join-Path $logDir "apollo_swing_study_$stamp.stdout.log"
$stderr = Join-Path $logDir "apollo_swing_study_$stamp.stderr.log"

$argsList = @("-m", "Apollo.swing_study", "run")
if (-not [string]::IsNullOrWhiteSpace($Tickers)) {
  $argsList += @("--tickers", $Tickers)
}
if ($Notify) {
  $argsList += "--notify"
}

try {
  $proc = Start-Process -FilePath $pythonExe -ArgumentList $argsList -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  if ($proc.ExitCode -ne 0) {
    $payload = @{
      level = "warn"
      title = "Apollo post-close swing study failed"
      message = "Apollo 4:20 PM swing study exited with code $($proc.ExitCode). stderr: $stderr"
      tags = @("warning", "apollo", "swing")
      source = "apollo"
      event = "swing_study_failed"
    } | ConvertTo-Json -Depth 6
    try {
      Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5216/alerts/external" -ContentType "application/json" -Body $payload | Out-Null
    } catch {
    }
    exit $proc.ExitCode
  }
} finally {
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

exit 0
