param(
  [int]$TimeoutMinutes = 90
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$apolloRoot = Join-Path $repoRoot "Apollo"
$logDir = Join-Path $apolloRoot "logs\trade_cycle\scheduler"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$lockPath = Join-Path $logDir "apollo_trade_cycle.lock"
if (Test-Path -LiteralPath $lockPath) {
  $lockAge = (Get-Date) - (Get-Item -LiteralPath $lockPath).LastWriteTime
  if ($lockAge.TotalHours -lt 3) {
    $skipLog = Join-Path $logDir ("apollo_trade_cycle_skipped_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
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
if (Test-Path -LiteralPath $candidate) {
  $pythonExe = $candidate
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdout = Join-Path $logDir "apollo_trade_cycle_$stamp.stdout.log"
$stderr = Join-Path $logDir "apollo_trade_cycle_$stamp.stderr.log"
$timeoutLog = Join-Path $logDir "apollo_trade_cycle_$stamp.timeout.json"

$env:PYTHONPATH = [string]$repoRoot
Set-Location -LiteralPath $repoRoot

$proc = $null
try {
  $proc = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList @("-m", "Apollo.trade_cycle", "run") `
    -NoNewWindow `
    -PassThru `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr

  $completed = $proc.WaitForExit([Math]::Max(1, $TimeoutMinutes) * 60 * 1000)
  if (-not $completed) {
    try {
      Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    } catch {
    }
    @{
      ok = $false
      error = "trade_cycle_timeout"
      timeout_minutes = $TimeoutMinutes
      started_at = $stamp
      stdout = $stdout
      stderr = $stderr
    } | ConvertTo-Json -Depth 4 | Set-Content -Path $timeoutLog -Encoding UTF8
    exit 124
  }

  exit $proc.ExitCode
} finally {
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}
